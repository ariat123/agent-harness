"""Raw, source-neutral company discovery campaign execution."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lead_scraper_core.campaign import CampaignConfig, CampaignLocation, build_search_text
from lead_scraper_core.maps import (
    MapsPageResult,
    api_items,
    normalize_company_item,
    provider_items,
    request_maps_page,
)
from lead_scraper_core.normalize import address_key, normalize_company_name, normalize_phone, slugify


COMPANY_FIELDS = [
    "campaign",
    "source_type",
    "source_query",
    "source_location",
    "google_place_id",
    "company_name",
    "website",
    "root_domain",
    "phone",
    "street_address",
    "city",
    "state",
    "postal_code",
    "google_category",
    "google_rating",
    "google_review_count",
    "maps_url",
    "raw_source_path",
    "website_email_candidates",
    "website_contact_page",
    "website_about_page",
    "location_count_signal",
    "parent_company_signal",
    "preliminary_notes",
    "scrape_timestamp",
]

GROUP_FIELDS = [
    "group_id",
    "grouping_method",
    "grouping_confidence",
    "group_key",
    "location_count",
    "company_names",
    "root_domains",
    "phones",
    "google_place_ids",
    "cities",
    "states",
]

SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "cookie",
    "rapidapikey",
    "secret",
    "token",
    "xrapidapikey",
}


@dataclass(frozen=True)
class CampaignRunResult:
    output_dir: Path
    manifest: dict[str, object]
    companies: list[dict[str, str]]
    groups: list[dict[str, str]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    temporary.replace(path)


def _atomic_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _redact(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z]", "", str(key).lower())
            redacted[str(key)] = "[REDACTED]" if normalized_key in SENSITIVE_KEYS else _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _safe_error(value: object) -> str:
    text = str(value or "request_failed")[:300]
    return re.sub(
        r"(?i)((?:api[_-]?key|authorization|token|secret)\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )


def _unique_output_dir(config: CampaignConfig, moment: datetime, override: Path | None) -> Path:
    if override is not None:
        path = override
        if path.exists() and any(path.iterdir()):
            raise ValueError(f"output directory is not empty: {path}")
        path.mkdir(parents=True, exist_ok=True)
        return path

    root = Path(config.output_root)
    stem = f"{moment.astimezone(timezone.utc):%Y-%m-%d_%H%M%S}_{config.campaign}"
    path = root / stem
    suffix = 2
    while path.exists():
        path = root / f"{stem}_{suffix}"
        suffix += 1
    path.mkdir(parents=True)
    return path


def _raw_filename(sequence: int, query: str, location: CampaignLocation, page: int) -> str:
    location_slug = slugify(location.value)
    return f"{sequence:04d}_{slugify(query)}_{location_slug}_page-{page}.json"


def _fallback_keys(company: dict[str, str]) -> set[tuple[str, ...]]:
    name = normalize_company_name(company.get("company_name"))
    address = address_key(company.get("address"))
    phone = normalize_phone(company.get("phone"))
    domain = company.get("root_domain", "").casefold()
    keys: set[tuple[str, ...]] = set()
    if name and address:
        keys.add(("name_address", name, address))
    if name and phone and domain:
        keys.add(("name_phone_domain", name, phone, domain))
    return keys


def _company_row(
    config: CampaignConfig,
    company: dict[str, str],
    query: str,
    location: CampaignLocation,
    raw_path: str,
    scrape_timestamp: str,
) -> dict[str, str]:
    return {
        "campaign": config.campaign,
        "source_type": config.source_type,
        "source_query": query,
        "source_location": location.value,
        "google_place_id": company["place_id"],
        "company_name": company["company_name"],
        "website": company["website"],
        "root_domain": company["root_domain"],
        "phone": company["phone"],
        "street_address": company["street_address"],
        "city": company["city"],
        "state": company["state"],
        "postal_code": company["zip_code"],
        "google_category": company["category"],
        "google_rating": company["rating"],
        "google_review_count": company["review_count"],
        "maps_url": company["google_maps_url"],
        "raw_source_path": raw_path,
        "website_email_candidates": "",
        "website_contact_page": "",
        "website_about_page": "",
        "location_count_signal": "",
        "parent_company_signal": "",
        "preliminary_notes": "",
        "scrape_timestamp": scrape_timestamp,
    }


def _join(values: list[str]) -> str:
    return " | ".join(sorted({value for value in values if value}, key=str.casefold))


def build_grouping_preview(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    domain_owner: dict[str, int] = {}
    phone_owner: dict[str, int] = {}
    normalized_phones = [normalize_phone(row.get("phone")) for row in rows]
    for index, row in enumerate(rows):
        domain = row.get("root_domain", "").casefold()
        phone = normalized_phones[index]
        if domain:
            if domain in domain_owner:
                union(index, domain_owner[domain])
            else:
                domain_owner[domain] = index
        if phone:
            if phone in phone_owner:
                union(index, phone_owner[phone])
            else:
                phone_owner[phone] = index

    components: dict[int, list[int]] = {}
    for index in range(len(rows)):
        components.setdefault(find(index), []).append(index)

    def identity(index: int) -> str:
        row = rows[index]
        return row.get("google_place_id") or "|".join(
            [normalize_company_name(row.get("company_name")), address_key(row.get("street_address"))]
        )

    ordered_components = sorted(components.values(), key=lambda indexes: sorted(identity(i) for i in indexes))
    groups: list[dict[str, str]] = []
    for group_number, indexes in enumerate(ordered_components, start=1):
        domain_counts = Counter(rows[index].get("root_domain", "").casefold() for index in indexes)
        phone_counts = Counter(normalized_phones[index] for index in indexes)
        shared_domains = sorted(value for value, count in domain_counts.items() if value and count > 1)
        shared_phones = sorted(value for value, count in phone_counts.items() if value and count > 1)
        if shared_domains and shared_phones:
            method = "exact_root_domain+exact_phone"
            confidence = "high"
            keys = [f"domain:{value}" for value in shared_domains] + [f"phone:{value}" for value in shared_phones]
        elif shared_domains:
            method = "exact_root_domain"
            confidence = "high"
            keys = [f"domain:{value}" for value in shared_domains]
        elif shared_phones:
            method = "exact_phone"
            confidence = "high"
            keys = [f"phone:{value}" for value in shared_phones]
        else:
            method = "singleton"
            confidence = "not_applicable"
            keys = [f"location:{identity(indexes[0])}"]

        group_id = f"group_{group_number:04d}"
        for index in indexes:
            rows[index]["location_count_signal"] = str(len(indexes))
            rows[index]["parent_company_signal"] = "" if len(indexes) == 1 else f"preview:{method}"
        groups.append(
            {
                "group_id": group_id,
                "grouping_method": method,
                "grouping_confidence": confidence,
                "group_key": _join(keys),
                "location_count": str(len(indexes)),
                "company_names": _join([rows[index].get("company_name", "") for index in indexes]),
                "root_domains": _join([rows[index].get("root_domain", "") for index in indexes]),
                "phones": _join([rows[index].get("phone", "") for index in indexes]),
                "google_place_ids": _join([rows[index].get("google_place_id", "") for index in indexes]),
                "cities": _join([rows[index].get("city", "") for index in indexes]),
                "states": _join([rows[index].get("state", "") for index in indexes]),
            }
        )
    return groups


def _missing_rates(rows: list[dict[str, str]]) -> dict[str, float]:
    fields = {
        "google_place_id": "google_place_id",
        "website": "website",
        "phone": "phone",
        "address": "street_address",
        "root_domain": "root_domain",
    }
    denominator = len(rows)
    return {
        label: (sum(not row.get(field) for row in rows) / denominator if denominator else 0.0)
        for label, field in fields.items()
    }


def _qa_text(
    config: CampaignConfig,
    manifest: dict[str, object],
    rows: list[dict[str, str]],
    groups: list[dict[str, str]],
) -> str:
    counts = manifest["counts"]
    rates = _missing_rates(rows)
    multi_groups = sum(int(group["location_count"]) > 1 for group in groups)
    failures = manifest["failure_summaries"]
    lines = [
        "# Google Maps Discovery QA",
        "",
        f"- Campaign: `{config.campaign}`",
        f"- Run ID: `{manifest['run_id']}`",
        f"- Status: `{manifest['status']}`",
        f"- Queries: {', '.join(config.queries)}",
        f"- Locations: {', '.join(location.value for location in config.locations)}",
        "",
        "## Counts and cap behavior",
        "",
        f"- Planned query-location combinations: {manifest['planned_query_location_combinations']}",
        f"- Maps requests: {counts['maps_request_attempts']} ({counts['successful_requests']} successful, {counts['failed_requests']} failed)",
        f"- Provider results observed: {counts['provider_results_observed']}",
        f"- Unique location rows emitted: {counts['unique_locations_emitted']}",
        f"- Duplicate results suppressed: {counts['duplicate_results_suppressed']}",
        f"- Total unique-result cap: {manifest['total_result_cap']} (reached: {str(manifest['cap_reached']).lower()})",
        (
            f"- Maps request cap: {manifest.get('provider_request_cap') or 'not configured'} "
            f"(reached: {str(manifest.get('provider_request_cap_reached', False)).lower()})"
        ),
        "",
        "## Missing fields",
        "",
    ]
    lines.extend(f"- {label}: {rate:.1%}" for label, rate in rates.items())
    lines.extend(
        [
            "",
            "## Grouping preview",
            "",
            f"- Preview groups: {len(groups)}",
            f"- Multi-location preview groups: {multi_groups}",
            "- Grouping uses exact root-domain and exact normalized-phone signals only. It is not authoritative parent-company resolution.",
            "",
            "## Failed requests",
            "",
        ]
    )
    if failures:
        lines.extend(
            f"- `{failure['source_query']}` / `{failure['source_location']}` / page {failure['page']}: {failure['error']}"
            for failure in failures
        )
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Scope and manual checks",
            "",
            "No final ICP qualification, website enrichment, email verification, or decision-maker research was performed.",
            "",
            "Before scaling, manually inspect query relevance, geographic accuracy, duplicate suppression, multi-location group suggestions, and missing contact fields.",
            "",
        ]
    )
    return "\n".join(lines)


def execute_campaign(
    config: CampaignConfig,
    *,
    output_dir: Path | None = None,
    request_page: Callable[..., MapsPageResult] = request_maps_page,
    now: Callable[[], datetime] = _utc_now,
    max_provider_requests: int | None = None,
) -> CampaignRunResult:
    if max_provider_requests is not None and max_provider_requests < 1:
        raise ValueError("max_provider_requests must be a positive integer")
    started = now()
    run_dir = _unique_output_dir(config, started, output_dir)
    raw_dir = run_dir / "raw" / "maps"
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_dir.name
    output_paths = {
        "campaign": "campaign.json",
        "manifest": "run.json",
        "companies_raw": "companies_raw.csv",
        "company_groups_preview": "company_groups_preview.csv",
        "qa_report": "qa_report.md",
        "raw_maps": "raw/maps",
    }
    counts = {
        "maps_request_attempts": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "retry_count": 0,
        "raw_response_files_written": 0,
        "provider_results_observed": 0,
        "unique_locations_emitted": 0,
        "duplicate_results_suppressed": 0,
        "website_requests": 0,
        "firecrawl_credits_used": 0,
    }
    manifest: dict[str, object] = {
        "run_id": run_id,
        "campaign": config.campaign,
        "configuration_version": config.version,
        "started_at": _timestamp(started),
        "finished_at": None,
        "status": "running",
        "country": config.country,
        "language": config.language,
        "query_count": len(config.queries),
        "location_count": len(config.locations),
        "planned_query_location_combinations": len(config.queries) * len(config.locations),
        "counts": counts,
        "total_result_cap": config.limits.max_unique_results_total,
        "cap_reached": False,
        "provider_request_cap": max_provider_requests,
        "provider_request_cap_reached": False,
        "output_paths": output_paths,
        "failure_summaries": [],
    }
    manifest_path = run_dir / "run.json"
    _atomic_json(run_dir / "campaign.json", config.to_dict())
    _atomic_json(manifest_path, manifest)

    rows: list[dict[str, str]] = []
    place_ids: set[str] = set()
    fallback_keys: set[tuple[str, ...]] = set()
    raw_sequence = 0

    try:
        stop_campaign = False
        for query in config.queries:
            if stop_campaign:
                break
            for location in config.locations:
                if stop_campaign:
                    break
                search_text = build_search_text(query, location)
                for page in range(1, config.limits.max_pages_per_query_location + 1):
                    if (
                        max_provider_requests is not None
                        and counts["maps_request_attempts"] >= max_provider_requests
                    ):
                        manifest["provider_request_cap_reached"] = True
                        stop_campaign = True
                        _atomic_json(manifest_path, manifest)
                        break
                    requested_at = now()
                    result = request_page(
                        search_text,
                        page,
                        country=config.country,
                        language=config.language,
                    )
                    raw_sequence += 1
                    raw_relative = f"raw/maps/{_raw_filename(raw_sequence, query, location, page)}"
                    raw_envelope = {
                        "request": {
                            "campaign": config.campaign,
                            "source_type": config.source_type,
                            "source_query": query,
                            "source_location_type": location.type,
                            "source_location": location.value,
                            "search_text": search_text,
                            "country": config.country,
                            "language": config.language,
                            "page": page,
                            "requested_at": _timestamp(requested_at),
                        },
                        "response": {
                            "success": result.success,
                            "http_status": result.http_status,
                            "retries": result.retries,
                            "error": _safe_error(result.error) if result.error else "",
                            "provider_response": _redact(result.payload),
                        },
                    }
                    counts["maps_request_attempts"] += 1
                    counts["retry_count"] += result.retries
                    if result.success:
                        counts["successful_requests"] += 1
                    else:
                        counts["failed_requests"] += 1

                    _atomic_json(run_dir / raw_relative, raw_envelope)
                    counts["raw_response_files_written"] += 1
                    if not result.success:
                        manifest["failure_summaries"].append(
                            {
                                "source_query": query,
                                "source_location": location.value,
                                "page": page,
                                "http_status": result.http_status,
                                "error": _safe_error(result.error),
                                "raw_source_path": raw_relative,
                            }
                        )
                    _atomic_json(manifest_path, manifest)

                    if not result.success:
                        break

                    observed = provider_items(result.payload)
                    counts["provider_results_observed"] += len(observed)
                    if not observed:
                        _atomic_json(manifest_path, manifest)
                        break

                    scrape_timestamp = _timestamp(requested_at)
                    for item in api_items(result.payload):
                        company = normalize_company_item(item)
                        place_id = company["place_id"]
                        item_fallback_keys = _fallback_keys(company)
                        duplicate = place_id in place_ids if place_id else bool(item_fallback_keys & fallback_keys)
                        if duplicate:
                            counts["duplicate_results_suppressed"] += 1
                            continue

                        rows.append(_company_row(config, company, query, location, raw_relative, scrape_timestamp))
                        if place_id:
                            place_ids.add(place_id)
                        fallback_keys.update(item_fallback_keys)
                        counts["unique_locations_emitted"] = len(rows)
                        if len(rows) >= config.limits.max_unique_results_total:
                            manifest["cap_reached"] = True
                            stop_campaign = True
                            break
                    _atomic_json(manifest_path, manifest)
                    if stop_campaign:
                        break

        groups = build_grouping_preview(rows)
        _atomic_csv(run_dir / "companies_raw.csv", rows, COMPANY_FIELDS)
        _atomic_csv(run_dir / "company_groups_preview.csv", groups, GROUP_FIELDS)
        manifest["status"] = "completed_with_errors" if counts["failed_requests"] else "complete"
        manifest["finished_at"] = _timestamp(now())
        _atomic_json(manifest_path, manifest)
        (run_dir / "qa_report.md").write_text(_qa_text(config, manifest, rows, groups), encoding="utf-8")
        return CampaignRunResult(run_dir, manifest, rows, groups)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["finished_at"] = _timestamp(now())
        manifest["failure_summaries"].append({"stage": "execution", "error": exc.__class__.__name__})
        _atomic_json(manifest_path, manifest)
        raise
