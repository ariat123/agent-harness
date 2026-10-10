"""Generalized, resumable website enrichment for Phase 1 discovery runs."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from enrichment_utils import is_non_site_url
from lead_scraper_core.campaign import WebsiteEnrichmentConfig
from lead_scraper_core.campaign_discovery import COMPANY_FIELDS
from lead_scraper_core.firecrawl import (
    DEFAULT_CREDIT_ESTIMATE_PER_PAGE,
    FirecrawlApp,
    firecrawl_api_key,
    is_credit_error,
    is_rate_limit_error,
    response_credits,
)
from lead_scraper_core.normalize import clean, root_domain, slugify
from lead_scraper_core.web_enrichment import (
    FirecrawlDocument,
    HttpPageResult,
    extract_firecrawl_document_evidence,
    extract_page_evidence,
    fetch_http_page,
    homepage_url,
    normalize_firecrawl_document,
    prioritized_same_domain_links,
    serialize_firecrawl_result,
    validate_public_url,
)


PARSER_VERSION = 2
RECEIPT_VERSION = 2
MAX_FIRECRAWL_RATE_LIMIT_RETRIES = 3
FIRECRAWL_INTER_REQUEST_DELAY_SECONDS = 1.0
ENRICHMENT_EXTRA_FIELDS = [
    "enrichment_mode",
    "enrichment_status",
    "enrichment_selection_status",
    "enrichment_reused",
    "homepage_requested_url",
    "homepage_final_url",
    "homepage_title",
    "homepage_meta_description",
    "http_pages_attempted",
    "http_pages_succeeded",
    "firecrawl_used",
    "firecrawl_pages",
    "firecrawl_credits",
    "contact_page_url",
    "about_page_url",
    "team_page_url",
    "service_page_urls",
    "location_page_urls",
    "careers_page_url",
    "email_candidates",
    "email_source_urls",
    "phone_candidates",
    "phone_source_urls",
    "service_signals",
    "evidence_source_urls",
    "enrichment_error",
    "enrichment_completed_at",
]
ENRICHED_FIELDS = COMPANY_FIELDS + ENRICHMENT_EXTRA_FIELDS
SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "cookie",
    "firecrawlapikey",
    "secret",
    "token",
    "xapikey",
    "xrapidapikey",
}


class MissingFirecrawlKey(RuntimeError):
    pass


@dataclass(frozen=True)
class EnrichmentRunResult:
    run_dir: Path
    manifest: dict[str, object]
    companies: list[dict[str, str]]


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


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _redact(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            sensitive = normalized in SENSITIVE_KEYS or normalized.endswith(("apikey", "token", "secret", "password"))
            result[str(key)] = "[REDACTED]" if sensitive else _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _safe_error(value: object) -> str:
    text = str(value or "")[:300]
    return re.sub(
        r"(?i)((?:api[_-]?key|authorization|token|secret)\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )


def firecrawl_key_present() -> bool:
    return bool(firecrawl_api_key())


def _domain_index(rows: list[dict[str, str]]) -> tuple[OrderedDict[str, list[int]], dict[str, str]]:
    domain_rows: OrderedDict[str, list[int]] = OrderedDict()
    homepages: dict[str, str] = {}
    for index, row in enumerate(rows):
        domain = clean(row.get("root_domain")).lower() or root_domain(row.get("website"))
        homepage = homepage_url(row.get("website", ""))
        if domain and homepage and not is_non_site_url(homepage):
            domain_rows.setdefault(domain, []).append(index)
            homepages.setdefault(domain, homepage)
    return domain_rows, homepages


def _domain_selection(
    domain_rows: OrderedDict[str, list[int]],
    config: WebsiteEnrichmentConfig,
) -> tuple[list[str], dict[str, str]]:
    available = set(domain_rows)
    requested = set(config.include_domains) | set(config.exclude_domains)
    unknown = sorted(requested - available)
    if unknown:
        raise ValueError(f"domain filters are not present in companies_raw.csv: {unknown}")

    include = set(config.include_domains)
    exclude = set(config.exclude_domains)
    candidates: list[str] = []
    selection_status: dict[str, str] = {}
    for domain in domain_rows:
        if domain in exclude:
            selection_status[domain] = "excluded_by_filter"
        elif include and domain not in include:
            selection_status[domain] = "not_selected_by_filter"
        else:
            candidates.append(domain)
    selected = candidates[: config.max_companies]
    for domain in selected:
        selection_status[domain] = "selected"
    for domain in candidates[config.max_companies :]:
        selection_status[domain] = "skipped_company_cap"
    return selected, selection_status


def plan_existing_run_enrichment(
    run_dir: Path,
    config: WebsiteEnrichmentConfig,
) -> dict[str, object]:
    """Return the exact ordered, no-network domain selection for an existing run."""
    run_dir = Path(run_dir)
    campaign_path = run_dir / "campaign.json"
    companies_path = run_dir / "companies_raw.csv"
    if not campaign_path.is_file() or not companies_path.is_file():
        raise ValueError("run directory must contain campaign.json and companies_raw.csv")
    campaign_data = json.loads(campaign_path.read_text(encoding="utf-8"))
    if not isinstance(campaign_data, dict):
        raise ValueError("campaign.json must contain an object")
    rows = _read_csv(companies_path)
    if rows and list(rows[0]) != COMPANY_FIELDS:
        raise ValueError("companies_raw.csv does not match the Phase 1 neutral schema")

    domain_rows, homepages = _domain_index(rows)
    selected_domains, selection_status = _domain_selection(domain_rows, config)
    fingerprint = _config_fingerprint(config)
    receipts_dir = run_dir / "raw" / "web" / "receipts"
    entries: list[dict[str, object]] = []
    for domain, indexes in domain_rows.items():
        status = selection_status[domain]
        action = "skip"
        if status == "selected":
            receipt = _load_receipt(_receipt_path(receipts_dir, domain))
            action = "reuse_completed_receipt" if config.resume and _completed_receipt(receipt, fingerprint) else "request"
        entries.append(
            {
                "root_domain": domain,
                "homepage": homepages[domain],
                "location_rows": len(indexes),
                "selection_status": status,
                "action": action,
            }
        )
    return {
        "source_discovery_run": run_dir.name,
        "source_campaign": clean(campaign_data.get("campaign")),
        "network_requests_made": 0,
        "maps_requests": 0,
        "eligible_domains": len(domain_rows),
        "selected_domains": len(selected_domains),
        "configuration": config.to_dict(),
        "domains": entries,
    }


def _config_fingerprint(config: WebsiteEnrichmentConfig) -> str:
    data = config.to_dict()
    for key in ("resume", "max_companies", "max_firecrawl_companies", "max_firecrawl_credits"):
        data.pop(key, None)
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _receipt_path(receipts_dir: Path, domain: str) -> Path:
    digest = hashlib.sha256(domain.encode("utf-8")).hexdigest()[:12]
    return receipts_dir / f"{slugify(domain)}_{digest}.json"


def _load_receipt(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _completed_receipt(receipt: dict[str, object] | None, fingerprint: str) -> bool:
    return bool(
        receipt
        and receipt.get("completed") is True
        and receipt.get("config_fingerprint") == fingerprint
        and receipt.get("root_domain")
    )


def _receipt_parser_version(receipt: dict[str, object] | None) -> int:
    if not receipt:
        return 0
    try:
        return int(receipt.get("parser_version") or 0)
    except (TypeError, ValueError):
        return 0


def _json_cell(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _unique_strings(values: list[str]) -> list[str]:
    return sorted({clean(value) for value in values if clean(value)}, key=str.casefold)


def _merge_candidates(existing: list[dict[str, str]], incoming: list[dict[str, str]], field: str) -> None:
    seen = {(item.get(field, ""), item.get("source_url", ""), item.get("source", "")) for item in existing}
    for item in incoming:
        key = (clean(item.get(field)), clean(item.get("source_url")), clean(item.get("source")))
        if not key[0] or key in seen:
            continue
        existing.append({field: key[0], "source_url": key[1], "source": key[2]})
        seen.add(key)
    existing.sort(key=lambda item: (item.get(field, "").casefold(), item.get("source_url", "")))


def _new_evidence(mode: str, homepage: str) -> dict[str, object]:
    return {
        "enrichment_mode": mode,
        "enrichment_status": "pending",
        "enrichment_selection_status": "pending",
        "homepage_requested_url": homepage,
        "homepage_final_url": "",
        "homepage_title": "",
        "homepage_meta_description": "",
        "http_pages_attempted": 0,
        "http_pages_succeeded": 0,
        "firecrawl_used": False,
        "firecrawl_pages": 0,
        "firecrawl_credits": 0,
        "firecrawl_credit_accounting": "none",
        "firecrawl_rate_limit_retries": 0,
        "contact_page_url": "",
        "about_page_url": "",
        "team_page_url": "",
        "service_page_urls": [],
        "location_page_urls": [],
        "careers_page_url": "",
        "email_candidates": [],
        "phone_candidates": [],
        "service_signals": [],
        "evidence_source_urls": [],
        "http_raw_paths": [],
        "firecrawl_raw_paths": [],
        "firecrawl_provider_metadata": [],
        "enrichment_error": "",
    }


def _page_kind(url: str) -> str:
    text = urlparse(url).path.lower()
    for kind in (
        "contact",
        "about",
        "team",
        "leadership",
        "services",
        "service",
        "inspections",
        "inspection",
        "locations",
        "careers",
    ):
        if re.search(rf"\b{kind}\b", text.replace("_", "-").replace("/", "-")):
            return kind
    return "homepage"


def _record_kind(evidence: dict[str, object], kind: str, url: str) -> None:
    if kind == "contact" and not evidence["contact_page_url"]:
        evidence["contact_page_url"] = url
    elif kind == "about" and not evidence["about_page_url"]:
        evidence["about_page_url"] = url
    elif kind in {"team", "leadership"} and not evidence["team_page_url"]:
        evidence["team_page_url"] = url
    elif kind in {"service", "services", "inspection", "inspections"}:
        evidence["service_page_urls"] = _unique_strings([*evidence["service_page_urls"], url])
    elif kind == "locations":
        evidence["location_page_urls"] = _unique_strings([*evidence["location_page_urls"], url])
    elif kind == "careers" and not evidence["careers_page_url"]:
        evidence["careers_page_url"] = url


def _merge_page_evidence(evidence: dict[str, object], page: dict[str, object], source_url: str) -> None:
    _merge_candidates(evidence["email_candidates"], page["email_candidates"], "email")
    _merge_candidates(evidence["phone_candidates"], page["phone_candidates"], "phone")
    signals = {
        (clean(item.get("signal")), clean(item.get("source_url"))): {
            "signal": clean(item.get("signal")),
            "source_url": clean(item.get("source_url")),
        }
        for item in [*evidence["service_signals"], *page["service_signals"]]
        if clean(item.get("signal"))
    }
    evidence["service_signals"] = sorted(signals.values(), key=lambda item: (item["signal"].casefold(), item["source_url"]))
    evidence["evidence_source_urls"] = _unique_strings([*evidence["evidence_source_urls"], source_url])


def _raw_http_path(run_dir: Path, attempt_id: str, domain: str, sequence: int, url: str) -> tuple[Path, str]:
    filename = f"{sequence:04d}_{slugify(urlparse(url).path or 'homepage')}.json"
    relative = Path("raw") / "web" / "http" / attempt_id / slugify(domain) / filename
    return run_dir / relative, str(relative)


def _raw_firecrawl_path(run_dir: Path, attempt_id: str, domain: str, sequence: int, url: str) -> tuple[Path, str]:
    filename = f"{sequence:04d}_{slugify(urlparse(url).path or 'homepage')}.json"
    relative = Path("raw") / "web" / "firecrawl" / attempt_id / slugify(domain) / filename
    return run_dir / relative, str(relative)


def _run_http_domain(
    run_dir: Path,
    attempt_id: str,
    domain: str,
    homepage: str,
    config: WebsiteEnrichmentConfig,
    fetcher: Callable[[str, WebsiteEnrichmentConfig, str], HttpPageResult],
) -> tuple[dict[str, object], list[str]]:
    evidence = _new_evidence(config.mode, homepage)
    queue: list[tuple[str, str]] = [(homepage, "homepage")]
    queued = {homepage}
    total_visible = 0
    homepage_failed = False
    high_value_failure = False
    anti_bot = False
    attempted_urls: list[str] = []

    while queue and int(evidence["http_pages_attempted"]) < config.max_http_pages_per_company:
        url, kind = queue.pop(0)
        attempted_urls.append(url)
        result = fetcher(url, config, domain)
        evidence["http_pages_attempted"] = int(evidence["http_pages_attempted"]) + 1
        raw_path, raw_relative = _raw_http_path(
            run_dir,
            attempt_id,
            domain,
            int(evidence["http_pages_attempted"]),
            url,
        )
        _atomic_json(
            raw_path,
            {
                "request": {"url": url, "root_domain": domain},
                "response": result.to_dict(),
            },
        )
        evidence["http_raw_paths"].append(raw_relative)
        if not result.success:
            homepage_failed = homepage_failed or kind == "homepage"
            high_value_failure = high_value_failure or kind != "homepage"
            if result.content:
                page = extract_page_evidence(result.content, result.final_url or url, source="http", page_kind=kind)
                anti_bot = anti_bot or bool(page["anti_bot"])
            continue

        evidence["http_pages_succeeded"] = int(evidence["http_pages_succeeded"]) + 1
        source_url = result.final_url or url
        if kind == "homepage":
            evidence["homepage_final_url"] = source_url
        _record_kind(evidence, kind, source_url)
        page = extract_page_evidence(result.content, source_url, source="http", page_kind=kind)
        if kind == "homepage":
            evidence["homepage_title"] = clean(page.get("page_title"))
            evidence["homepage_meta_description"] = clean(page.get("meta_description"))
        total_visible += int(page["visible_text_characters"])
        anti_bot = anti_bot or bool(page["anti_bot"])
        _merge_page_evidence(evidence, page, source_url)

        for link in prioritized_same_domain_links(result.content, source_url, domain):
            link_url = str(link["url"])
            link_kind = str(link["kind"])
            _record_kind(evidence, link_kind, link_url)
            if link_url not in queued:
                queue.append((link_url, link_kind))
                queued.add(link_url)

    thin = int(evidence["http_pages_succeeded"]) == 0 or total_visible < 200
    meaningful_contact = bool(
        evidence["email_candidates"]
        or evidence["phone_candidates"]
        or evidence["contact_page_url"]
    )
    fallback_reasons: list[str] = []
    if homepage_failed:
        fallback_reasons.append("homepage_http_failed")
    if anti_bot:
        fallback_reasons.append("anti_bot_or_rendering_content")
    if thin:
        fallback_reasons.append("thin_content")
    if high_value_failure:
        fallback_reasons.append("high_value_page_failed")
    if not meaningful_contact:
        fallback_reasons.append("no_meaningful_contact_path")
    if config.fallback_on_no_email and not evidence["email_candidates"]:
        fallback_reasons.append("configured_no_email_fallback")

    if int(evidence["http_pages_succeeded"]):
        evidence["enrichment_status"] = "completed_http_thin" if thin else "completed_http"
    else:
        evidence["enrichment_status"] = "failed_http"
        evidence["enrichment_error"] = "all_http_pages_failed"
    return evidence, _unique_strings([*attempted_urls, *evidence["service_page_urls"]]) + fallback_reasons


def _firecrawl_urls(evidence: dict[str, object], homepage: str) -> list[str]:
    candidates = [
        homepage,
        clean(evidence.get("contact_page_url")),
        clean(evidence.get("about_page_url")),
        clean(evidence.get("team_page_url")),
        *evidence.get("service_page_urls", []),
        *evidence.get("location_page_urls", []),
        clean(evidence.get("careers_page_url")),
    ]
    return list(dict.fromkeys(value for value in candidates if value))


def _firecrawl_evidence_from_prior_receipt(
    receipt: dict[str, object] | None,
    mode: str,
    homepage: str,
) -> dict[str, object]:
    """Seed forced Firecrawl with bounded-HTTP discoveries without reusing paid results."""
    prior = receipt.get("evidence") if isinstance(receipt, dict) else None
    if not isinstance(prior, dict) or not prior.get("http_pages_attempted"):
        return _new_evidence(mode, homepage)
    evidence = dict(prior)
    evidence["enrichment_mode"] = mode
    evidence["enrichment_status"] = "pending"
    evidence["enrichment_selection_status"] = "selected"
    evidence["homepage_requested_url"] = homepage
    evidence["firecrawl_used"] = False
    evidence["firecrawl_pages"] = 0
    evidence["firecrawl_credits"] = 0
    evidence["firecrawl_credit_accounting"] = "none"
    evidence["firecrawl_raw_paths"] = []
    evidence["firecrawl_provider_metadata"] = []
    evidence["enrichment_error"] = ""
    return evidence


def _merge_firecrawl_document(
    evidence: dict[str, object],
    document: FirecrawlDocument,
    domain: str,
) -> list[dict[str, object]]:
    source_url = document.source_url or document.requested_url
    kind = _page_kind(source_url)
    if not evidence["homepage_final_url"] or _page_kind(document.requested_url) == "homepage":
        evidence["homepage_final_url"] = document.final_url or document.requested_url
    _record_kind(evidence, kind, source_url)
    page = extract_firecrawl_document_evidence(document, domain, page_kind=kind)
    if kind == "homepage":
        evidence["homepage_title"] = clean(page.get("page_title"))
        evidence["homepage_meta_description"] = clean(page.get("meta_description"))
    _merge_page_evidence(evidence, page, source_url)
    for link in page.get("discovered_links", []):
        _record_kind(evidence, clean(link.get("kind")), clean(link.get("url")))
    return list(page.get("discovered_links", []))


def _default_firecrawl_factory(key: str, timeout_seconds: int):
    if FirecrawlApp is None:
        raise RuntimeError("Firecrawl SDK is not installed")
    return FirecrawlApp(api_key=key, timeout=timeout_seconds)


def _reported_credits(result: object, data: dict[str, object]) -> int:
    credits = response_credits(result)
    if credits:
        return credits
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return 0
    try:
        return int(metadata.get("credits_used") or metadata.get("creditsUsed") or 0)
    except (TypeError, ValueError):
        return 0


def _run_firecrawl_domain(
    run_dir: Path,
    attempt_id: str,
    domain: str,
    homepage: str,
    config: WebsiteEnrichmentConfig,
    evidence: dict[str, object],
    credit_state: dict[str, object],
    client_box: dict[str, object],
    client_factory: Callable[[str, int], object],
    url_validator: Callable[[str, str], str],
) -> tuple[bool, bool, bool]:
    success = False
    credit_stopped = False
    request_attempted = False
    queue = _firecrawl_urls(evidence, homepage)
    queued = set(queue)
    while queue and int(evidence["firecrawl_pages"]) < config.max_firecrawl_pages_per_company:
        url = queue.pop(0)
        estimate = DEFAULT_CREDIT_ESTIMATE_PER_PAGE
        if int(credit_state["accounted"]) + estimate > config.max_firecrawl_credits:
            credit_state["cap_reached"] = True
            credit_stopped = True
            break
        try:
            safe_url = url_validator(url, domain)
        except ValueError as exc:
            evidence["enrichment_error"] = str(exc)
            continue

        key = firecrawl_api_key()
        if not key:
            raise MissingFirecrawlKey("FIRECRAWL_API_KEY is required immediately before Firecrawl use")
        if "client" not in client_box:
            client_box["client"] = client_factory(key, config.request_timeout_seconds)
        client = client_box["client"]
        evidence["firecrawl_used"] = True
        request_attempted = True
        sequence = int(evidence["firecrawl_pages"]) + 1
        raw_path, raw_relative = _raw_firecrawl_path(run_dir, attempt_id, domain, sequence, safe_url)
        result = None
        terminal_exception: Exception | None = None
        for retry in range(MAX_FIRECRAWL_RATE_LIMIT_RETRIES + 1):
            try:
                result = client.scrape(
                    url=safe_url,
                    formats=["markdown", "html"],
                    only_main_content=True,
                    timeout=config.request_timeout_seconds * 1000,
                )
                terminal_exception = None
                break
            except Exception as exc:
                terminal_exception = exc
                if is_rate_limit_error(exc) and retry < MAX_FIRECRAWL_RATE_LIMIT_RETRIES:
                    evidence["firecrawl_rate_limit_retries"] = (
                        int(evidence.get("firecrawl_rate_limit_retries", 0)) + 1
                    )
                    time.sleep(min(30, 15 * (2**retry)))
                    continue
                break

        if terminal_exception is not None or result is None:
            exc = terminal_exception or RuntimeError("empty_firecrawl_result")
            status = "credit_error" if is_credit_error(exc) else ("rate_limit" if is_rate_limit_error(exc) else "api_error")
            _atomic_json(
                raw_path,
                {
                    "request": {"url": safe_url, "root_domain": domain},
                    "response": {
                        "success": False,
                        "error": status,
                        "exception_type": exc.__class__.__name__,
                        "rate_limit_retries": evidence.get("firecrawl_rate_limit_retries", 0),
                    },
                },
            )
            evidence["firecrawl_raw_paths"].append(raw_relative)
            evidence["firecrawl_pages"] = sequence
            evidence["enrichment_error"] = status
            if is_credit_error(exc):
                credit_state["cap_reached"] = True
                credit_stopped = True
            break

        time.sleep(FIRECRAWL_INTER_REQUEST_DELAY_SECONDS)
        data = serialize_firecrawl_result(result)
        reported = _reported_credits(result, data)
        accounted = reported or estimate
        accounting = "actual" if reported else "estimated"
        credit_state["accounted"] = int(credit_state["accounted"]) + accounted
        credit_state[accounting] = int(credit_state[accounting]) + accounted
        evidence["firecrawl_credits"] = int(evidence["firecrawl_credits"]) + accounted
        evidence["firecrawl_credit_accounting"] = accounting
        evidence["firecrawl_pages"] = sequence
        _atomic_json(
            raw_path,
            {
                "request": {"url": safe_url, "root_domain": domain},
                "response": {
                    "success": True,
                    "credits_reported": reported,
                    "credits_accounted": accounted,
                    "credit_accounting": accounting,
                    "provider_response": _redact(data),
                },
            },
        )
        evidence["firecrawl_raw_paths"].append(raw_relative)
        document = normalize_firecrawl_document(data, safe_url, domain)
        metadata = _redact(document.metadata)
        evidence["firecrawl_provider_metadata"].append(
            {"raw_path": raw_relative, "metadata": metadata}
        )
        if document.html or document.markdown:
            links = _merge_firecrawl_document(evidence, document, domain)
            for link in links:
                link_url = clean(link.get("url"))
                if link_url and link_url not in queued:
                    queue.append(link_url)
                    queued.add(link_url)
            success = True
        if int(credit_state["accounted"]) >= config.max_firecrawl_credits:
            credit_state["cap_reached"] = True
            credit_stopped = bool(queue)
            break
    return success, credit_stopped, request_attempted


def _receipt(
    domain: str,
    fingerprint: str,
    evidence: dict[str, object],
    *,
    completed: bool,
    retryable: bool,
    completed_at: str,
) -> dict[str, object]:
    return {
        "version": RECEIPT_VERSION,
        "parser_version": PARSER_VERSION,
        "root_domain": domain,
        "config_fingerprint": fingerprint,
        "completed": completed,
        "retryable": retryable,
        "enrichment_completed_at": completed_at,
        "evidence": evidence,
    }


def _row_from_evidence(row: dict[str, str], evidence: dict[str, object], reused: bool, completed_at: str) -> dict[str, str]:
    email_candidates = evidence.get("email_candidates", [])
    phone_candidates = evidence.get("phone_candidates", [])
    enriched = {field: row.get(field, "") for field in COMPANY_FIELDS}
    enriched.update(
        {
            "enrichment_mode": clean(evidence.get("enrichment_mode")),
            "enrichment_status": clean(evidence.get("enrichment_status")),
            "enrichment_selection_status": clean(evidence.get("enrichment_selection_status")),
            "enrichment_reused": "true" if reused else "false",
            "homepage_requested_url": clean(evidence.get("homepage_requested_url")),
            "homepage_final_url": clean(evidence.get("homepage_final_url")),
            "homepage_title": clean(evidence.get("homepage_title")),
            "homepage_meta_description": clean(evidence.get("homepage_meta_description")),
            "http_pages_attempted": str(evidence.get("http_pages_attempted", 0)),
            "http_pages_succeeded": str(evidence.get("http_pages_succeeded", 0)),
            "firecrawl_used": "true" if evidence.get("firecrawl_used") else "false",
            "firecrawl_pages": str(evidence.get("firecrawl_pages", 0)),
            "firecrawl_credits": str(evidence.get("firecrawl_credits", 0)),
            "contact_page_url": clean(evidence.get("contact_page_url")),
            "about_page_url": clean(evidence.get("about_page_url")),
            "team_page_url": clean(evidence.get("team_page_url")),
            "service_page_urls": _json_cell(evidence.get("service_page_urls", [])),
            "location_page_urls": _json_cell(evidence.get("location_page_urls", [])),
            "careers_page_url": clean(evidence.get("careers_page_url")),
            "email_candidates": _json_cell(email_candidates),
            "email_source_urls": _json_cell(_unique_strings([item.get("source_url", "") for item in email_candidates])),
            "phone_candidates": _json_cell(phone_candidates),
            "phone_source_urls": _json_cell(_unique_strings([item.get("source_url", "") for item in phone_candidates])),
            "service_signals": _json_cell(evidence.get("service_signals", [])),
            "evidence_source_urls": _json_cell(evidence.get("evidence_source_urls", [])),
            "enrichment_error": _safe_error(evidence.get("enrichment_error")),
            "enrichment_completed_at": completed_at,
        }
    )
    return enriched


def _qa_report(manifest: dict[str, object]) -> str:
    counts = manifest["counts"]
    config = manifest["enrichment_configuration"]
    return "\n".join(
        [
            "# Company Website Enrichment QA",
            "",
            f"- Source discovery run: `{manifest['source_discovery_run']}`",
            f"- Enrichment mode: `{config['mode']}`",
            f"- Status: `{manifest['status']}`",
            f"- Parser version: {manifest.get('parser_version', 0)}",
            f"- Location rows represented: {counts['locations_represented']}",
            f"- Unique domains eligible: {counts['unique_domains_eligible']}",
            f"- Domains attempted: {counts['domains_attempted']}",
            f"- Domains completed: {counts['domains_completed']}",
            f"- Domains failed: {counts.get('domains_failed', counts['retryable_failures'])}",
            f"- Completed domains reused from receipts: {counts['resume_receipts_reused']}",
            f"- Exact-domain location reuse groups: {counts['domains_reused_across_locations']}",
            f"- HTTP pages: {counts['http_pages_attempted']} attempted, {counts['http_pages_succeeded']} succeeded",
            f"- HTTP failures: {counts['http_failures']}",
            f"- Firecrawl companies attempted: {counts['firecrawl_companies_attempted']}",
            f"- Firecrawl pages/operations: {counts['firecrawl_pages']}",
            f"- Firecrawl rate-limit retries: {counts.get('firecrawl_rate_limit_retries', 0)}",
            f"- Firecrawl credits accounted: {counts['firecrawl_credits']} ({manifest['firecrawl_credit_accounting']})",
            f"- Firecrawl credit ceiling: {manifest['firecrawl_credit_ceiling']}",
            f"- Credit ceiling reached: {str(manifest['firecrawl_credit_ceiling_reached']).lower()}",
            f"- Retryable failures: {counts['retryable_failures']}",
            f"- Domains skipped due to caps: {counts['domains_skipped_due_to_caps']}",
            f"- Domains reparsed offline: {counts.get('domains_reprocessed', 0)}",
            f"- New network requests during rebuild: {counts.get('rebuild_network_requests', 0)}",
            f"- New Firecrawl operations during rebuild: {counts.get('rebuild_firecrawl_pages', 0)}",
            f"- New Firecrawl credits during rebuild: {counts.get('rebuild_firecrawl_credits', 0)}",
            "- Maps requests during this stage: 0",
            "- Email-verification services used: 0",
            "",
            "Extracted emails are public candidates with source pages; they are not guessed or mailbox-verified.",
            "No final ICP qualification or decision-maker determination was performed.",
            "",
        ]
    )


def _int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _json_list_value(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _evidence_from_enriched_row(row: dict[str, str]) -> dict[str, object]:
    evidence = _new_evidence(clean(row.get("enrichment_mode")), clean(row.get("homepage_requested_url")))
    for field in (
        "enrichment_status",
        "enrichment_selection_status",
        "homepage_final_url",
        "homepage_title",
        "homepage_meta_description",
        "contact_page_url",
        "about_page_url",
        "team_page_url",
        "careers_page_url",
        "enrichment_error",
    ):
        evidence[field] = clean(row.get(field))
    for field in ("http_pages_attempted", "http_pages_succeeded", "firecrawl_pages", "firecrawl_credits"):
        evidence[field] = _int_value(row.get(field))
    evidence["firecrawl_used"] = clean(row.get("firecrawl_used")).lower() == "true"
    for field in (
        "service_page_urls",
        "location_page_urls",
        "email_candidates",
        "phone_candidates",
        "service_signals",
        "evidence_source_urls",
    ):
        evidence[field] = _json_list_value(row.get(field))
    evidence["firecrawl_credit_accounting"] = "actual" if evidence["firecrawl_credits"] else "none"
    return evidence


def _read_preserved_firecrawl_envelope(run_dir: Path, relative_path: str) -> dict[str, object]:
    run_root = run_dir.resolve()
    path = (run_dir / relative_path).resolve()
    try:
        path.relative_to(run_root)
    except ValueError as exc:
        raise ValueError(f"raw Firecrawl evidence path escapes run directory: {relative_path}") from exc
    if not path.is_file():
        raise ValueError(f"missing raw Firecrawl evidence: {relative_path}")
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"corrupt raw Firecrawl evidence: {relative_path}") from exc
    if not isinstance(envelope, dict):
        raise ValueError(f"malformed raw Firecrawl evidence: {relative_path}")
    request = envelope.get("request")
    response = envelope.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise ValueError(f"malformed raw Firecrawl envelope: {relative_path}")
    if response.get("success") is not True or not isinstance(response.get("provider_response"), dict):
        raise ValueError(f"raw Firecrawl evidence is not a successful provider response: {relative_path}")
    return envelope


def _rebuild_firecrawl_evidence(
    run_dir: Path,
    domain: str,
    receipt: dict[str, object],
) -> dict[str, object]:
    old_evidence = receipt.get("evidence")
    if not isinstance(old_evidence, dict):
        raise ValueError(f"completed receipt has no evidence object: {domain}")
    raw_paths = [clean(path) for path in old_evidence.get("firecrawl_raw_paths", []) if clean(path)]
    historical_pages = _int_value(old_evidence.get("firecrawl_pages"))
    historical_credits = _int_value(old_evidence.get("firecrawl_credits"))
    if not raw_paths or len(raw_paths) != historical_pages:
        raise ValueError(f"completed receipt raw evidence count mismatch: {domain}")

    envelopes = [_read_preserved_firecrawl_envelope(run_dir, path) for path in raw_paths]
    raw_credits = sum(_int_value(envelope["response"].get("credits_accounted")) for envelope in envelopes)
    if raw_credits and raw_credits != historical_credits:
        raise ValueError(f"historical Firecrawl credit mismatch: {domain}")

    first_request = envelopes[0]["request"]
    homepage = clean(old_evidence.get("homepage_requested_url")) or clean(first_request.get("url"))
    evidence = _new_evidence(clean(old_evidence.get("enrichment_mode")), homepage)
    evidence["enrichment_status"] = clean(old_evidence.get("enrichment_status"))
    evidence["http_pages_attempted"] = _int_value(old_evidence.get("http_pages_attempted"))
    evidence["http_pages_succeeded"] = _int_value(old_evidence.get("http_pages_succeeded"))
    evidence["http_raw_paths"] = list(old_evidence.get("http_raw_paths", []))
    evidence["firecrawl_used"] = True
    evidence["firecrawl_pages"] = historical_pages
    evidence["firecrawl_credits"] = historical_credits
    evidence["firecrawl_credit_accounting"] = clean(old_evidence.get("firecrawl_credit_accounting")) or "estimated"
    evidence["firecrawl_raw_paths"] = raw_paths

    for relative_path, envelope in zip(raw_paths, envelopes):
        request = envelope["request"]
        response = envelope["response"]
        requested_url = clean(request.get("url"))
        provider_response = response["provider_response"]
        document = normalize_firecrawl_document(provider_response, requested_url, domain)
        if not document.html and not document.markdown:
            raise ValueError(f"raw Firecrawl evidence has no parseable content: {relative_path}")
        evidence["firecrawl_provider_metadata"].append(
            {"raw_path": relative_path, "metadata": _redact(document.metadata)}
        )
        _merge_firecrawl_document(evidence, document, domain)

    if evidence["enrichment_status"] not in {"completed_firecrawl", "completed_fallback"}:
        raise ValueError(f"receipt is not a completed Firecrawl result: {domain}")
    evidence["enrichment_error"] = ""
    return evidence


def rebuild_existing_run_from_raw(
    run_dir: Path,
    *,
    now: Callable[[], datetime] = _utc_now,
) -> EnrichmentRunResult:
    run_dir = Path(run_dir)
    companies_path = run_dir / "companies_raw.csv"
    enriched_path = run_dir / "companies_enriched.csv"
    manifest_path = run_dir / "enrichment_run.json"
    receipts_dir = run_dir / "raw" / "web" / "receipts"
    for required in (companies_path, enriched_path, manifest_path):
        if not required.is_file():
            raise ValueError(f"offline rebuild requires existing {required.name}")

    rows = _read_csv(companies_path)
    existing_rows = _read_csv(enriched_path)
    if len(rows) != len(existing_rows):
        raise ValueError("companies_raw.csv and companies_enriched.csv row counts differ")
    if rows and list(rows[0]) != COMPANY_FIELDS:
        raise ValueError("companies_raw.csv does not match the Phase 1 neutral schema")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("enrichment_run.json must contain an object")

    previous_parser_version = _int_value(manifest.get("parser_version"))
    existing_reprocessed_at = clean(manifest.get("last_reprocessed_at"))
    reprocessed_at = existing_reprocessed_at if previous_parser_version == PARSER_VERSION else _timestamp(now())

    row_evidence_by_domain: dict[str, dict[str, object]] = {}
    completed_at_by_domain: dict[str, str] = {}
    domain_rows: OrderedDict[str, list[int]] = OrderedDict()
    for index, (row, enriched_row) in enumerate(zip(rows, existing_rows)):
        domain = clean(row.get("root_domain")).lower() or root_domain(row.get("website"))
        if domain:
            domain_rows.setdefault(domain, []).append(index)
            row_evidence_by_domain.setdefault(domain, _evidence_from_enriched_row(enriched_row))
            completed_at_by_domain.setdefault(domain, clean(enriched_row.get("enrichment_completed_at")))

    updated_receipts: dict[Path, dict[str, object]] = {}
    reprocessed_domains: list[str] = []
    for receipt_path in sorted(receipts_dir.glob("*.json")):
        receipt = _load_receipt(receipt_path)
        if not receipt or receipt.get("completed") is not True:
            continue
        evidence = receipt.get("evidence")
        if not isinstance(evidence, dict) or not evidence.get("firecrawl_used"):
            continue
        domain = clean(receipt.get("root_domain")).lower()
        if domain not in domain_rows:
            raise ValueError(f"receipt domain is not present in companies_raw.csv: {domain}")
        rebuilt = _rebuild_firecrawl_evidence(run_dir, domain, receipt)
        row_evidence_by_domain[domain] = rebuilt
        completed_at = clean(receipt.get("enrichment_completed_at"))
        completed_at_by_domain[domain] = completed_at
        updated = dict(receipt)
        updated["version"] = RECEIPT_VERSION
        updated["parser_version"] = PARSER_VERSION
        updated.setdefault("original_enrichment_completed_at", completed_at)
        updated["parser_reprocessed_at"] = clean(receipt.get("parser_reprocessed_at")) or reprocessed_at
        updated["evidence"] = rebuilt
        updated_receipts[receipt_path] = updated
        reprocessed_domains.append(domain)

    if not reprocessed_domains:
        raise ValueError("no completed Firecrawl receipts with preserved raw evidence were found")

    enriched_rows: list[dict[str, str]] = []
    for index, (row, existing_row) in enumerate(zip(rows, existing_rows)):
        domain = clean(row.get("root_domain")).lower() or root_domain(row.get("website"))
        evidence = row_evidence_by_domain.get(domain) or _evidence_from_enriched_row(existing_row)
        enriched_rows.append(
            _row_from_evidence(
                row,
                evidence,
                len(domain_rows.get(domain, [])) > 1,
                completed_at_by_domain.get(domain, clean(existing_row.get("enrichment_completed_at"))),
            )
        )

    unique_evidence = list(row_evidence_by_domain.values())
    statuses = [clean(evidence.get("enrichment_status")) for evidence in unique_evidence]
    completed_statuses = {"completed_http", "completed_http_thin", "completed_firecrawl", "completed_fallback"}
    failed_statuses = {"failed_http", "failed_firecrawl"}
    skipped_statuses = {"skipped_company_cap", "firecrawl_company_cap", "credit_capped"}
    counts = dict(manifest.get("counts") or {})
    counts.update(
        {
            "unique_domains_eligible": len(domain_rows),
            "locations_represented": len(rows),
            "domains_attempted": sum(status in completed_statuses | failed_statuses for status in statuses),
            "domains_completed": sum(status in completed_statuses for status in statuses),
            "domains_failed": sum(status in failed_statuses for status in statuses),
            "domains_skipped_due_to_caps": sum(status in skipped_statuses for status in statuses),
            "retryable_failures": sum(status in failed_statuses for status in statuses),
            "http_pages_attempted": sum(_int_value(evidence.get("http_pages_attempted")) for evidence in unique_evidence),
            "http_pages_succeeded": sum(_int_value(evidence.get("http_pages_succeeded")) for evidence in unique_evidence),
            "firecrawl_companies_attempted": sum(bool(evidence.get("firecrawl_used")) for evidence in unique_evidence),
            "firecrawl_pages": sum(_int_value(evidence.get("firecrawl_pages")) for evidence in unique_evidence),
            "firecrawl_credits": sum(_int_value(evidence.get("firecrawl_credits")) for evidence in unique_evidence),
            "domains_reprocessed": len(reprocessed_domains),
            "rebuild_network_requests": 0,
            "rebuild_firecrawl_pages": 0,
            "rebuild_firecrawl_credits": 0,
            "maps_requests": 0,
            "email_verification_services_used": 0,
        }
    )
    counts["http_failures"] = counts["http_pages_attempted"] - counts["http_pages_succeeded"]
    manifest["counts"] = counts
    manifest["parser_version"] = PARSER_VERSION
    manifest["last_reprocessed_at"] = reprocessed_at
    manifest["rebuild_from_raw"] = {
        "performed_at": reprocessed_at,
        "domains_reprocessed": sorted(reprocessed_domains),
        "historical_firecrawl_pages": counts["firecrawl_pages"],
        "historical_firecrawl_credits": counts["firecrawl_credits"],
        "new_network_requests": 0,
        "new_firecrawl_pages": 0,
        "new_firecrawl_credits": 0,
    }
    manifest["failure_summaries"] = [
        {
            "root_domain": domain,
            "error": _safe_error(evidence.get("enrichment_error")),
            "retryable": True,
        }
        for domain, evidence in row_evidence_by_domain.items()
        if clean(evidence.get("enrichment_status")) in failed_statuses
    ]
    manifest["status"] = (
        "partial_credit_cap"
        if "credit_capped" in statuses
        else ("completed_with_errors" if counts["domains_failed"] else "complete")
    )

    for receipt_path, receipt in updated_receipts.items():
        _atomic_json(receipt_path, receipt)
    _atomic_csv(enriched_path, enriched_rows, ENRICHED_FIELDS)
    _atomic_json(manifest_path, manifest)
    _atomic_text(run_dir / "enrichment_qa_report.md", _qa_report(manifest))
    return EnrichmentRunResult(run_dir, manifest, enriched_rows)


def execute_existing_run_enrichment(
    run_dir: Path,
    config: WebsiteEnrichmentConfig,
    *,
    http_fetcher: Callable[[str, WebsiteEnrichmentConfig, str], HttpPageResult] | None = None,
    firecrawl_client_factory: Callable[[str, int], object] = _default_firecrawl_factory,
    url_validator: Callable[[str, str], str] | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> EnrichmentRunResult:
    run_dir = Path(run_dir)
    campaign_path = run_dir / "campaign.json"
    companies_path = run_dir / "companies_raw.csv"
    if not campaign_path.is_file() or not companies_path.is_file():
        raise ValueError("run directory must contain campaign.json and companies_raw.csv")
    campaign_data = json.loads(campaign_path.read_text(encoding="utf-8"))
    if not isinstance(campaign_data, dict):
        raise ValueError("campaign.json must contain an object")
    rows = _read_csv(companies_path)
    if rows and list(rows[0]) != COMPANY_FIELDS:
        raise ValueError("companies_raw.csv does not match the Phase 1 neutral schema")

    started = now()
    attempt_id = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    receipts_dir = run_dir / "raw" / "web" / "receipts"
    (run_dir / "raw" / "web" / "http").mkdir(parents=True, exist_ok=True)
    (run_dir / "raw" / "web" / "firecrawl").mkdir(parents=True, exist_ok=True)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = _config_fingerprint(config)

    domain_rows, homepages = _domain_index(rows)
    selected_domains, selection_status = _domain_selection(domain_rows, config)

    counts = {
        "unique_domains_eligible": len(domain_rows),
        "locations_represented": len(rows),
        "domains_attempted": 0,
        "domains_completed": 0,
        "domains_failed": 0,
        "domains_reused_across_locations": sum(len(indexes) > 1 for indexes in domain_rows.values()),
        "resume_receipts_reused": 0,
        "http_pages_attempted": 0,
        "http_pages_succeeded": 0,
        "http_failures": 0,
        "firecrawl_companies_attempted": 0,
        "firecrawl_pages": 0,
        "firecrawl_credits": 0,
        "domains_skipped_due_to_caps": 0,
        "retryable_failures": 0,
        "maps_requests": 0,
        "email_verification_services_used": 0,
    }
    manifest: dict[str, object] = {
        "source_discovery_run": run_dir.name,
        "source_discovery_run_path": str(run_dir),
        "source_campaign": clean(campaign_data.get("campaign")),
        "attempt_id": attempt_id,
        "enrichment_configuration": config.to_dict(),
        "started_at": _timestamp(started),
        "finished_at": None,
        "status": "running",
        "parser_version": PARSER_VERSION,
        "firecrawl_key_present": firecrawl_key_present(),
        "firecrawl_credit_ceiling": config.max_firecrawl_credits,
        "firecrawl_credit_ceiling_reached": False,
        "firecrawl_credit_accounting": "none",
        "counts": counts,
        "failure_summaries": [],
        "output_paths": {
            "companies_enriched": "companies_enriched.csv",
            "enrichment_manifest": "enrichment_run.json",
            "enrichment_qa": "enrichment_qa_report.md",
            "http_evidence": f"raw/web/http/{attempt_id}",
            "firecrawl_evidence": f"raw/web/firecrawl/{attempt_id}",
            "receipts": "raw/web/receipts",
        },
    }
    manifest_path = run_dir / "enrichment_run.json"
    _atomic_json(manifest_path, manifest)

    session = requests.Session()
    fetcher = http_fetcher or (
        lambda url, stage_config, domain: fetch_http_page(
            url,
            timeout_seconds=stage_config.request_timeout_seconds,
            expected_root_domain=domain,
            session=session,
        )
    )
    validator = url_validator or (lambda url, domain: validate_public_url(url, domain))
    credit_state: dict[str, object] = {"accounted": 0, "actual": 0, "estimated": 0, "cap_reached": False}
    client_box: dict[str, object] = {}
    evidence_by_domain: dict[str, dict[str, object]] = {}
    completed_at_by_domain: dict[str, str] = {}

    unselected_domains = [domain for domain in domain_rows if domain not in selected_domains]
    counts["domains_skipped_due_to_caps"] += sum(
        selection_status[domain] == "skipped_company_cap" for domain in unselected_domains
    )
    for domain in unselected_domains:
        evidence = _new_evidence(config.mode, homepages[domain])
        evidence["enrichment_selection_status"] = selection_status[domain]
        evidence["enrichment_status"] = selection_status[domain]
        if selection_status[domain] == "skipped_company_cap":
            evidence["enrichment_error"] = "max_companies"
        elif selection_status[domain] == "excluded_by_filter":
            evidence["enrichment_error"] = "exclude_domain_filter"
        else:
            evidence["enrichment_error"] = "include_domain_filter"
        evidence_by_domain[domain] = evidence
        completed_at_by_domain[domain] = ""

    try:
        if config.mode == "off":
            for domain in selected_domains:
                evidence = _new_evidence("off", homepages[domain])
                evidence["enrichment_selection_status"] = "selected"
                evidence["enrichment_status"] = "skipped_mode_off"
                evidence_by_domain[domain] = evidence
                completed_at_by_domain[domain] = _timestamp(now())
        else:
            firecrawl_domains_attempted: set[str] = set()
            for domain in selected_domains:
                receipt_path = _receipt_path(receipts_dir, domain)
                existing = _load_receipt(receipt_path)
                if config.resume and _completed_receipt(existing, fingerprint):
                    receipt_parser_version = _receipt_parser_version(existing)
                    if receipt_parser_version != PARSER_VERSION:
                        raise ValueError(
                            "completed receipt parser version mismatch; run company_enrichment.py "
                            "--run-dir <path> --rebuild-from-raw before resuming"
                        )
                    evidence = existing.get("evidence")
                    if isinstance(evidence, dict):
                        evidence = dict(evidence)
                        evidence["enrichment_selection_status"] = "selected"
                        evidence_by_domain[domain] = evidence
                        completed_at_by_domain[domain] = clean(existing.get("enrichment_completed_at"))
                        counts["resume_receipts_reused"] += 1
                        counts["domains_completed"] += 1
                        continue

                counts["domains_attempted"] += 1
                homepage = homepages[domain]
                if config.mode in {"http", "fallback"}:
                    evidence, http_context = _run_http_domain(
                        run_dir,
                        attempt_id,
                        domain,
                        homepage,
                        config,
                        fetcher,
                    )
                    evidence["enrichment_selection_status"] = "selected"
                    fallback_reasons = [
                        value
                        for value in http_context
                        if value
                        in {
                            "homepage_http_failed",
                            "anti_bot_or_rendering_content",
                            "thin_content",
                            "high_value_page_failed",
                            "no_meaningful_contact_path",
                            "configured_no_email_fallback",
                        }
                    ]
                else:
                    evidence = _firecrawl_evidence_from_prior_receipt(existing, config.mode, homepage)
                    fallback_reasons = ["forced_firecrawl_mode"]

                http_attempted = int(evidence["http_pages_attempted"])
                http_succeeded = int(evidence["http_pages_succeeded"])
                counts["http_pages_attempted"] += http_attempted
                counts["http_pages_succeeded"] += http_succeeded
                counts["http_failures"] += http_attempted - http_succeeded

                use_firecrawl = config.mode == "firecrawl" or (config.mode == "fallback" and bool(fallback_reasons))
                firecrawl_success = False
                credit_stopped = False
                if use_firecrawl:
                    if len(firecrawl_domains_attempted) >= config.max_firecrawl_companies:
                        evidence["enrichment_status"] = "firecrawl_company_cap"
                        evidence["enrichment_error"] = "max_firecrawl_companies"
                        counts["domains_skipped_due_to_caps"] += 1
                    elif bool(credit_state["cap_reached"]):
                        evidence["enrichment_status"] = "credit_capped"
                        evidence["enrichment_error"] = "max_firecrawl_credits"
                        counts["domains_skipped_due_to_caps"] += 1
                    else:
                        firecrawl_success, credit_stopped, firecrawl_attempted = _run_firecrawl_domain(
                            run_dir,
                            attempt_id,
                            domain,
                            homepage,
                            config,
                            evidence,
                            credit_state,
                            client_box,
                            firecrawl_client_factory,
                            validator,
                        )
                        if firecrawl_attempted:
                            firecrawl_domains_attempted.add(domain)
                        if credit_stopped:
                            evidence["enrichment_status"] = "credit_capped"
                            evidence["enrichment_error"] = "max_firecrawl_credits"
                            counts["domains_skipped_due_to_caps"] += 1
                        elif firecrawl_success:
                            evidence["enrichment_status"] = "completed_firecrawl" if config.mode == "firecrawl" else "completed_fallback"
                            evidence["enrichment_error"] = ""
                        else:
                            evidence["enrichment_status"] = "failed_firecrawl"
                            evidence["enrichment_error"] = evidence["enrichment_error"] or "no_usable_firecrawl_content"

                completed = bool(
                    evidence["enrichment_status"]
                    in {"completed_http", "completed_http_thin", "completed_firecrawl", "completed_fallback"}
                )
                retryable = not completed
                completed_at = _timestamp(now())
                receipt = _receipt(
                    domain,
                    fingerprint,
                    evidence,
                    completed=completed,
                    retryable=retryable,
                    completed_at=completed_at,
                )
                _atomic_json(receipt_path, receipt)
                evidence_by_domain[domain] = evidence
                completed_at_by_domain[domain] = completed_at
                if completed:
                    counts["domains_completed"] += 1
                elif evidence["enrichment_status"] in {"failed_http", "failed_firecrawl"}:
                    counts["domains_failed"] += 1
                    counts["retryable_failures"] += 1
                    manifest["failure_summaries"].append(
                        {"root_domain": domain, "error": _safe_error(evidence["enrichment_error"]), "retryable": True}
                    )
                _atomic_json(manifest_path, manifest)

        for index, row in enumerate(rows):
            domain = clean(row.get("root_domain")).lower() or root_domain(row.get("website"))
            if domain in evidence_by_domain:
                continue
            evidence = _new_evidence(config.mode, homepage_url(row.get("website", "")))
            evidence["enrichment_status"] = "skipped_no_usable_website"
            evidence["enrichment_error"] = "missing_website_or_root_domain"
            evidence_by_domain[f"__row_{index}"] = evidence

        enriched_rows: list[dict[str, str]] = []
        for index, row in enumerate(rows):
            domain = clean(row.get("root_domain")).lower() or root_domain(row.get("website"))
            evidence = evidence_by_domain.get(domain) or evidence_by_domain[f"__row_{index}"]
            completed_at = completed_at_by_domain.get(domain, "")
            enriched_rows.append(_row_from_evidence(row, evidence, len(domain_rows.get(domain, [])) > 1, completed_at))

        counts["firecrawl_companies_attempted"] = len(firecrawl_domains_attempted) if config.mode != "off" else 0
        counts["firecrawl_pages"] = sum(int(evidence.get("firecrawl_pages", 0)) for evidence in evidence_by_domain.values())
        counts["firecrawl_rate_limit_retries"] = sum(
            int(evidence.get("firecrawl_rate_limit_retries", 0))
            for evidence in evidence_by_domain.values()
        )
        counts["firecrawl_credits"] = int(credit_state["accounted"])
        manifest["firecrawl_credit_ceiling_reached"] = bool(credit_state["cap_reached"])
        if int(credit_state["actual"]) and int(credit_state["estimated"]):
            manifest["firecrawl_credit_accounting"] = "actual_and_estimated"
        elif int(credit_state["actual"]):
            manifest["firecrawl_credit_accounting"] = "actual"
        elif int(credit_state["estimated"]):
            manifest["firecrawl_credit_accounting"] = "estimated"
        manifest["status"] = (
            "partial_credit_cap"
            if manifest["firecrawl_credit_ceiling_reached"]
            else ("completed_with_errors" if counts["retryable_failures"] else "complete")
        )
        manifest["finished_at"] = _timestamp(now())
        _atomic_csv(run_dir / "companies_enriched.csv", enriched_rows, ENRICHED_FIELDS)
        _atomic_json(manifest_path, manifest)
        _atomic_text(run_dir / "enrichment_qa_report.md", _qa_report(manifest))
        return EnrichmentRunResult(run_dir, manifest, enriched_rows)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["finished_at"] = _timestamp(now())
        manifest["failure_summaries"].append({"stage": "enrichment", "error": exc.__class__.__name__})
        _atomic_json(manifest_path, manifest)
        raise
