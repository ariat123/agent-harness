"""Output helpers and shared field lists."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from lead_scraper_core.normalize import slugify


RUNS_DIR = Path("runs")

FIELDNAMES = [
    "place_id",
    "company_name",
    "website",
    "root_domain",
    "phone",
    "email",
    "address",
    "city",
    "state",
    "zip_code",
    "rating",
    "review_count",
    "categories",
    "query_used",
    "source_zip",
    "source_city",
    "source_state",
    "source_metro",
    "geo_status",
    "geo_rejection_reason",
    "notes",
]

RAW_EXTRA_FIELDS = [
    "google_maps_url",
    "latitude",
    "longitude",
    "business_status",
    "source_page",
    "fit_status",
    "fit_label",
    "fit_reason",
    "source_run_id",
]

RAW_FIELDS = FIELDNAMES + RAW_EXTRA_FIELDS

FIRECRAWL_QUEUE_EXTRA_FIELDS = [
    "firecrawl_candidate_source",
    "firecrawl_queue_status",
    "firecrawl_usefulness_reason",
    "firecrawl_skip_reason",
]

FIRECRAWL_QUEUE_FIELDS = RAW_FIELDS + FIRECRAWL_QUEUE_EXTRA_FIELDS

FIRECRAWL_ENRICHED_EXTRA_FIELDS = FIRECRAWL_QUEUE_EXTRA_FIELDS + [
    "firecrawl_status",
    "firecrawl_credits_used",
    "firecrawl_pages_attempted",
    "firecrawl_source_pages",
    "firecrawl_error",
    "firecrawl_json",
    "enriched_fit_status",
    "enriched_fit_label",
    "enriched_fit_reason",
    "enriched_confidence",
    "residential_pm_evidence",
    "single_family_evidence",
    "service_area_evidence",
    "negative_fit_evidence",
    "decision_makers",
    "firecrawl_emails",
    "firecrawl_phones",
    "firecrawl_notes",
]

FIRECRAWL_ENRICHED_FIELDS = RAW_FIELDS + FIRECRAWL_ENRICHED_EXTRA_FIELDS

SUPPRESSED_FIELDS = [
    "company_name",
    "root_domain",
    "website",
    "email",
    "phone",
    "address",
    "city",
    "state",
    "zip_code",
    "query_used",
    "source_zip",
    "source_city",
    "source_state",
    "source_metro",
    "suppression_reason",
    "matched_suppression_field",
    "matched_suppression_value",
    "matched_suppression_status",
]

ALREADY_FOUND_FIELDS = RAW_FIELDS + [
    "already_found_reason",
    "matched_found_field",
    "matched_found_value",
    "matched_found_run_id",
]

FOUND_REGISTRY_FIELDS = [
    "niche",
    "first_found_at",
    "last_seen_at",
    "run_id",
    "company_name",
    "normalized_company_name",
    "root_domain",
    "website",
    "email",
    "phone",
    "normalized_phone",
    "place_id",
    "google_maps_url",
    "address",
    "city",
    "state",
    "zip_code",
    "source_zip",
    "source_city",
    "source_state",
    "source_metro",
    "query_used",
    "fit_status",
    "fit_label",
    "geo_status",
    "firecrawl_status",
    "enriched_fit_status",
    "enriched_fit_label",
    "do_not_refind_reason",
    "source_file",
]

SCRAPED_GEO_FIELDS = [
    "niche",
    "run_id",
    "target_csv",
    "search_term",
    "zip",
    "city",
    "state",
    "metro",
    "query_used",
    "searched_at",
    "maps_page_calls",
    "raw_rows",
    "in_geo_rows",
    "campaign_targets",
    "manual_review_rows",
    "suppressed_rows",
    "already_found_rows",
    "status",
]


def unique_output_dir(run_name: str) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    base = RUNS_DIR / f"{today}_{slugify(run_name)}"
    if not base.exists():
        return base
    suffix = 2
    while True:
        candidate = RUNS_DIR / f"{today}_{slugify(run_name)}-{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def ensure_csv_header(path: Path, fields: list[str]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()


def append_csv_row(path: Path, row: dict[str, str], fields: list[str]) -> None:
    ensure_csv_header(path, fields)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writerow(row)
