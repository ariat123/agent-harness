"""Found-lead and scraped-geography registries for profile scraper runs."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lead_scraper_core.normalize import (
    clean,
    normalize_company_name,
    normalize_domain,
    normalize_email,
    normalize_phone,
)
from lead_scraper_core.outputs import FOUND_REGISTRY_FIELDS, SCRAPED_GEO_FIELDS, write_csv


IN_GEO_STATUSES = {"in_target_zip", "nearby_target_metro", "in_target_state"}


@dataclass
class FoundRegistryMatch:
    field: str
    value: str
    run_id: str
    reason: str


@dataclass
class FoundRegistryData:
    loaded: bool = False
    path: str = ""
    row_count: int = 0
    place_ids: dict[str, dict[str, str]] | None = None
    domains: dict[str, dict[str, str]] | None = None
    company_names: dict[str, dict[str, str]] | None = None
    emails: dict[str, dict[str, str]] | None = None
    phones: dict[str, dict[str, str]] | None = None

    def __post_init__(self) -> None:
        self.place_ids = self.place_ids or {}
        self.domains = self.domains or {}
        self.company_names = self.company_names or {}
        self.emails = self.emails or {}
        self.phones = self.phones or {}

    @property
    def place_id_count(self) -> int:
        return len(self.place_ids or {})

    @property
    def domain_count(self) -> int:
        return len(self.domains or {})

    @property
    def company_name_count(self) -> int:
        return len(self.company_names or {})

    @property
    def email_count(self) -> int:
        return len(self.emails or {})

    @property
    def phone_count(self) -> int:
        return len(self.phones or {})


@dataclass
class ScrapedGeoRegistryData:
    loaded: bool = False
    path: str = ""
    row_count: int = 0
    completed_keys: set[tuple[str, str, str, str, str, str]] | None = None

    def __post_init__(self) -> None:
        self.completed_keys = self.completed_keys or set()

    @property
    def completed_count(self) -> int:
        return len(self.completed_keys or set())


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def found_registry_record(row: dict[str, str]) -> dict[str, str]:
    return {
        "place_id": clean(row.get("place_id")),
        "root_domain": normalize_domain(row.get("root_domain") or row.get("website")),
        "normalized_company_name": normalize_company_name(row.get("normalized_company_name") or row.get("company_name")),
        "email": normalize_email(row.get("email")),
        "phone": normalize_phone(row.get("normalized_phone") or row.get("phone")),
        "run_id": clean(row.get("run_id") or row.get("source_run_id")),
        "do_not_refind_reason": clean(row.get("do_not_refind_reason") or row.get("status") or row.get("fit_label")),
    }


def load_found_leads_registry(path: str | None) -> FoundRegistryData:
    if not path:
        return FoundRegistryData(loaded=False)
    registry_path = Path(path)
    if not registry_path.exists():
        return FoundRegistryData(loaded=False, path=str(registry_path))

    data = FoundRegistryData(loaded=True, path=str(registry_path))
    with registry_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            data.row_count += 1
            record = found_registry_record(row)
            if record["place_id"]:
                data.place_ids.setdefault(record["place_id"], record)
            if record["root_domain"]:
                data.domains.setdefault(record["root_domain"], record)
            if record["normalized_company_name"]:
                data.company_names.setdefault(record["normalized_company_name"], record)
            if record["email"]:
                data.emails.setdefault(record["email"], record)
            if record["phone"]:
                data.phones.setdefault(record["phone"], record)
    return data


def match_found_lead(row: dict[str, str], registry: FoundRegistryData) -> FoundRegistryMatch | None:
    if not registry.loaded:
        return None
    checks = [
        ("place_id", clean(row.get("place_id")), registry.place_ids),
        ("root_domain", normalize_domain(row.get("root_domain") or row.get("website")), registry.domains),
        ("normalized_company_name", normalize_company_name(row.get("company_name")), registry.company_names),
        ("phone", normalize_phone(row.get("phone")), registry.phones),
        ("email", normalize_email(row.get("email")), registry.emails),
    ]
    for field, value, lookup in checks:
        if not value or not lookup:
            continue
        record = lookup.get(value)
        if not record:
            continue
        return FoundRegistryMatch(
            field=field,
            value=value,
            run_id=record.get("run_id", ""),
            reason=record.get("do_not_refind_reason", "") or "found_leads_registry",
        )
    return None


def already_found_row(row: dict[str, str], match: FoundRegistryMatch) -> dict[str, str]:
    return {
        **row,
        "already_found_reason": match.reason,
        "matched_found_field": match.field,
        "matched_found_value": match.value,
        "matched_found_run_id": match.run_id,
    }


def filter_already_found(
    raw_rows: list[dict[str, str]],
    registry: FoundRegistryData,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    active_rows: list[dict[str, str]] = []
    already_found: list[dict[str, str]] = []
    for row in raw_rows:
        match = match_found_lead(row, registry)
        if not match:
            active_rows.append(row)
            continue
        row["fit_status"] = "excluded"
        row["fit_label"] = "already_found"
        prior_reason = clean(row.get("fit_reason"))
        row["fit_reason"] = f"{prior_reason} | already found" if prior_reason else "already found"
        already_found.append(already_found_row(row, match))
    return active_rows, already_found


def stable_keys(row: dict[str, str]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    values = {
        "place_id": clean(row.get("place_id")),
        "root_domain": normalize_domain(row.get("root_domain") or row.get("website")),
        "normalized_company_name": normalize_company_name(row.get("normalized_company_name") or row.get("company_name")),
        "email": normalize_email(row.get("email")),
        "phone": normalize_phone(row.get("normalized_phone") or row.get("phone")),
    }
    for field, value in values.items():
        if value:
            keys.add((field, value))
    return keys


def primary_identity_key(row: dict[str, str]) -> tuple[str, str] | None:
    priority = [
        ("place_id", clean(row.get("place_id"))),
        ("root_domain", normalize_domain(row.get("root_domain") or row.get("website"))),
        ("email", normalize_email(row.get("email"))),
        ("phone", normalize_phone(row.get("normalized_phone") or row.get("phone"))),
        ("normalized_company_name", normalize_company_name(row.get("normalized_company_name") or row.get("company_name"))),
    ]
    for field, value in priority:
        if value:
            return field, value
    return None


def completion_keys(row: dict[str, str]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for field, value in [
        ("root_domain", normalize_domain(row.get("root_domain") or row.get("website"))),
        ("place_id", clean(row.get("place_id"))),
        ("email", normalize_email(row.get("email"))),
        ("phone", normalize_phone(row.get("phone"))),
        ("normalized_company_name", normalize_company_name(row.get("company_name"))),
    ]:
        if value:
            keys.add((field, value))
            break
    return keys


def registry_reason(profile, row: dict[str, str]) -> str:
    label = clean(row.get("fit_label"))
    status = clean(row.get("fit_status"))
    if label == "already_found":
        return "already_found"
    if label == "suppressed":
        return "suppressed"
    if label in getattr(profile, "CAMPAIGN_TARGET_LABELS", set()) or status == "campaign_target":
        return "new_campaign_target"
    if status == "manual_review" or label in getattr(profile, "MANUAL_REVIEW_LABELS", set()):
        return "manual_review"
    return label or "bad_fit"


def registry_update_record(
    profile,
    run_id: str,
    row: dict[str, str],
    reason: str,
    timestamp: str,
    source_file: str,
) -> dict[str, str]:
    return {
        "niche": profile.NICHE_ID,
        "first_found_at": clean(row.get("first_found_at")) or timestamp,
        "last_seen_at": timestamp,
        "run_id": run_id,
        "company_name": clean(row.get("company_name")),
        "normalized_company_name": normalize_company_name(row.get("company_name")),
        "root_domain": normalize_domain(row.get("root_domain") or row.get("website")),
        "website": clean(row.get("website")),
        "email": normalize_email(row.get("email") or row.get("firecrawl_emails")),
        "phone": clean(row.get("phone") or row.get("firecrawl_phones")),
        "normalized_phone": normalize_phone(row.get("phone") or row.get("firecrawl_phones")),
        "place_id": clean(row.get("place_id")),
        "google_maps_url": clean(row.get("google_maps_url")),
        "address": clean(row.get("address")),
        "city": clean(row.get("city")),
        "state": clean(row.get("state")).upper(),
        "zip_code": clean(row.get("zip_code")),
        "source_zip": clean(row.get("source_zip")),
        "source_city": clean(row.get("source_city")),
        "source_state": clean(row.get("source_state")).upper(),
        "source_metro": clean(row.get("source_metro")),
        "query_used": clean(row.get("query_used")),
        "fit_status": clean(row.get("enriched_fit_status") or row.get("fit_status")),
        "fit_label": clean(row.get("enriched_fit_label") or row.get("fit_label")),
        "geo_status": clean(row.get("geo_status")),
        "firecrawl_status": clean(row.get("firecrawl_status")),
        "enriched_fit_status": clean(row.get("enriched_fit_status")),
        "enriched_fit_label": clean(row.get("enriched_fit_label")),
        "do_not_refind_reason": reason,
        "source_file": source_file,
    }


def build_found_registry_updates(
    profile,
    run_id: str,
    raw_rows: list[dict[str, str]],
    firecrawl_excluded_rows: list[dict[str, str]] | None = None,
    source_file: str = "",
    timestamp: str | None = None,
) -> list[dict[str, str]]:
    timestamp = timestamp or now_iso()
    records: dict[tuple[str, str], dict[str, str]] = {}
    for row in raw_rows:
        key = primary_identity_key(row)
        if not key:
            continue
        records[key] = registry_update_record(
            profile,
            run_id,
            row,
            registry_reason(profile, row),
            timestamp,
            source_file,
        )
    for row in firecrawl_excluded_rows or []:
        key = primary_identity_key(row)
        if not key:
            continue
        records[key] = registry_update_record(
            profile,
            run_id,
            row,
            "firecrawl_excluded",
            timestamp,
            source_file,
        )
    return list(records.values())


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def append_unique_rows(path: Path, new_rows: list[dict[str, str]], fields: list[str], key_func) -> int:
    existing = read_csv_rows(path)
    seen: set[tuple[str, str] | tuple[str, str, str, str, str, str]] = set()
    for row in existing:
        key = key_func(row)
        if key:
            seen.add(key)

    appended: list[dict[str, str]] = []
    for row in new_rows:
        key = key_func(row)
        if not key or key in seen:
            continue
        appended.append(row)
        seen.add(key)

    if appended:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_csv(path, existing + appended, fields)
    elif not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        write_csv(path, [], fields)
    return len(appended)


def append_found_registry(path: str, rows: list[dict[str, str]]) -> int:
    registry_path = Path(path)
    existing = read_csv_rows(registry_path)
    seen: set[tuple[str, str]] = set()
    for row in existing:
        seen.update(stable_keys(row))

    appended: list[dict[str, str]] = []
    for row in rows:
        keys = stable_keys(row)
        if not keys or keys & seen:
            continue
        appended.append(row)
        seen.update(keys)

    if appended:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        write_csv(registry_path, existing + appended, FOUND_REGISTRY_FIELDS)
    elif not registry_path.exists():
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        write_csv(registry_path, [], FOUND_REGISTRY_FIELDS)
    return len(appended)


def scraped_geo_key_from_values(
    niche: str,
    search_term: str,
    zip_code: str,
    city: str,
    state: str,
    metro: str,
) -> tuple[str, str, str, str, str, str]:
    return (
        clean(niche).lower(),
        clean(search_term).lower(),
        clean(zip_code),
        clean(city).lower(),
        clean(state).upper(),
        clean(metro).lower(),
    )


def scraped_geo_key(row: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    return scraped_geo_key_from_values(
        row.get("niche", ""),
        row.get("search_term", ""),
        row.get("zip", ""),
        row.get("city", ""),
        row.get("state", ""),
        row.get("metro", ""),
    )


def target_key(profile, target) -> tuple[str, str, str, str, str, str]:
    return scraped_geo_key_from_values(
        profile.NICHE_ID,
        target.search_term,
        target.zip,
        target.city,
        target.state,
        target.metro,
    )


def load_scraped_geo_registry(path: str | None) -> ScrapedGeoRegistryData:
    if not path:
        return ScrapedGeoRegistryData(loaded=False)
    registry_path = Path(path)
    if not registry_path.exists():
        return ScrapedGeoRegistryData(loaded=False, path=str(registry_path))

    data = ScrapedGeoRegistryData(loaded=True, path=str(registry_path))
    with registry_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            data.row_count += 1
            if clean(row.get("status")) == "completed":
                data.completed_keys.add(scraped_geo_key(row))
    return data


def filter_targets_by_scraped_registry(
    profile,
    targets: list,
    registry: ScrapedGeoRegistryData,
    skip: bool,
) -> tuple[list, list]:
    if not skip or not registry.loaded:
        return targets, []
    active = []
    skipped = []
    completed = registry.completed_keys or set()
    for target in targets:
        if target_key(profile, target) in completed:
            skipped.append(target)
        else:
            active.append(target)
    return active, skipped


def row_matches_target(row: dict[str, str], target_stat: dict[str, object]) -> bool:
    return (
        clean(row.get("source_zip")) == clean(target_stat.get("zip"))
        and clean(row.get("source_city")).lower() == clean(target_stat.get("city")).lower()
        and clean(row.get("source_state")).upper() == clean(target_stat.get("state")).upper()
        and clean(row.get("source_metro")).lower() == clean(target_stat.get("metro")).lower()
        and clean(row.get("query_used")) == clean(target_stat.get("query_used"))
    )


def build_scraped_geo_rows(
    profile,
    run_id: str,
    target_csv: str,
    target_stats: list[dict[str, object]],
    raw_rows: list[dict[str, str]],
    campaign_targets: list[dict[str, str]],
    manual_review: list[dict[str, str]],
    suppressed: list[dict[str, str]],
    already_found: list[dict[str, str]],
    searched_at: str | None = None,
) -> list[dict[str, str]]:
    searched_at = searched_at or now_iso()
    rows: list[dict[str, str]] = []
    for stat in target_stats:
        raw_for_target = [row for row in raw_rows if row_matches_target(row, stat)]
        campaign_for_target = [row for row in campaign_targets if row_matches_target(row, stat)]
        manual_for_target = [row for row in manual_review if row_matches_target(row, stat)]
        suppressed_for_target = [row for row in suppressed if row_matches_target(row, stat)]
        already_for_target = [row for row in already_found if row_matches_target(row, stat)]
        rows.append(
            {
                "niche": profile.NICHE_ID,
                "run_id": run_id,
                "target_csv": target_csv,
                "search_term": clean(stat.get("search_term")),
                "zip": clean(stat.get("zip")),
                "city": clean(stat.get("city")),
                "state": clean(stat.get("state")).upper(),
                "metro": clean(stat.get("metro")),
                "query_used": clean(stat.get("query_used")),
                "searched_at": searched_at,
                "maps_page_calls": str(stat.get("maps_page_calls") or 0),
                "raw_rows": str(len(raw_for_target)),
                "in_geo_rows": str(sum(1 for row in raw_for_target if row.get("geo_status") in IN_GEO_STATUSES)),
                "campaign_targets": str(len(campaign_for_target)),
                "manual_review_rows": str(len(manual_for_target)),
                "suppressed_rows": str(len(suppressed_for_target)),
                "already_found_rows": str(len(already_for_target)),
                "status": clean(stat.get("status")) or "completed",
            }
        )
    return rows


def append_scraped_geo_registry(path: str, rows: list[dict[str, str]]) -> int:
    return append_unique_rows(Path(path), rows, SCRAPED_GEO_FIELDS, scraped_geo_key)


def build_final_exports(
    profile,
    campaign_targets: list[dict[str, str]],
    enriched_rows: list[dict[str, str]],
    failed_rows: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    excluded_labels = set(getattr(profile, "EXCLUDED_LABELS", set()))
    final_enriched: list[dict[str, str]] = []
    excluded_after_enrichment: list[dict[str, str]] = []
    completed_keys: set[tuple[str, str]] = set()

    for row in enriched_rows:
        completed_keys.update(completion_keys(row))
        enriched_status = clean(row.get("enriched_fit_status") or row.get("fit_status"))
        enriched_label = clean(row.get("enriched_fit_label") or row.get("fit_label"))
        in_geo = row.get("geo_status") in IN_GEO_STATUSES
        has_site = bool(normalize_domain(row.get("root_domain") or row.get("website")))
        if enriched_status == "campaign_target" and enriched_label not in excluded_labels and in_geo and has_site:
            final_enriched.append(row)
        elif enriched_status == "excluded" or enriched_label in excluded_labels:
            excluded_after_enrichment.append(row)

    for row in failed_rows:
        completed_keys.update(completion_keys(row))

    final_unenriched = [
        row for row in campaign_targets
        if not (completion_keys(row) & completed_keys)
    ]
    return {
        "final_enriched": final_enriched,
        "final_unenriched": final_unenriched,
        "excluded_after_enrichment": excluded_after_enrichment,
    }
