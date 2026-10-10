"""Firecrawl queueing and profile-based paid enrichment."""
from __future__ import annotations

import csv
import json
import os
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

from enrichment_utils import normalize_url
from lead_scraper_core.normalize import clean
from lead_scraper_core.outputs import (
    FIRECRAWL_ENRICHED_FIELDS,
    append_csv_row,
    ensure_csv_header,
)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    from firecrawl import FirecrawlApp
except ImportError:
    FirecrawlApp = None


IN_GEO_STATUSES = {"in_target_zip", "nearby_target_metro", "in_target_state"}
DEFAULT_CREDIT_ESTIMATE_PER_PAGE = int(os.getenv("FIRECRAWL_CREDIT_ESTIMATE_PER_PAGE", "5"))
MAX_RATE_LIMIT_RETRIES = 3


def response_credits(result: object) -> int:
    metadata = getattr(result, "metadata", None)
    if metadata is None:
        return 0
    if isinstance(metadata, dict):
        return int(metadata.get("credits_used") or metadata.get("creditsUsed") or 0)
    return int(
        getattr(metadata, "credits_used", None)
        or getattr(metadata, "creditsUsed", 0)
        or 0
    )


def is_credit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        term in message
        for term in (
            "insufficient credits",
            "out of credits",
            "credit limit",
            "payment required",
            "402",
            "quota",
            "billing",
        )
    )


def is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(term in message for term in ("rate limit", "too many requests", "429"))


def firecrawl_api_key() -> str | None:
    return os.getenv("FIRECRAWL_API_KEY")


def url_for_path(base_url: str, path: str) -> str:
    if not path:
        return base_url
    return urljoin(base_url.rstrip("/") + "/", path)


def usefulness_reason(profile, row: dict[str, str], source: str) -> str:
    if hasattr(profile, "firecrawl_usefulness_reason"):
        return profile.firecrawl_usefulness_reason(row, source)
    return (
        f"{source} with usable website; Firecrawl can confirm fit, find decision makers, "
        "detect exclusions, and improve contact data."
    )


def queue_row(profile, row: dict[str, str], source: str) -> dict[str, str]:
    queued = {**row}
    queued["firecrawl_candidate_source"] = source
    queued["firecrawl_usefulness_reason"] = usefulness_reason(profile, row, source)
    queued["firecrawl_queue_status"] = "queued"
    queued["firecrawl_skip_reason"] = ""
    if row.get("geo_status") not in IN_GEO_STATUSES:
        queued["firecrawl_queue_status"] = "skipped"
        queued["firecrawl_skip_reason"] = "not_in_geo"
    elif row.get("fit_status") == "excluded" or row.get("fit_label") in getattr(profile, "EXCLUDED_LABELS", set()):
        queued["firecrawl_queue_status"] = "skipped"
        queued["firecrawl_skip_reason"] = "excluded_fit"
    elif not clean(row.get("website")) or not clean(row.get("root_domain")):
        queued["firecrawl_queue_status"] = "skipped"
        queued["firecrawl_skip_reason"] = "no_usable_website"
    return queued


def build_firecrawl_queue(
    profile,
    campaign_targets: list[dict[str, str]],
    manual_review: list[dict[str, str]],
    include_manual_review: bool = False,
) -> list[dict[str, str]]:
    queue: list[dict[str, str]] = []
    for row in campaign_targets:
        if row.get("fit_status") != "campaign_target":
            continue
        queue.append(queue_row(profile, row, "campaign_target"))
    if include_manual_review:
        for row in manual_review:
            if row.get("fit_status") != "manual_review":
                continue
            queue.append(queue_row(profile, row, "manual_review"))
    return queue


def queue_summary(
    mode: str,
    queue_rows: list[dict[str, str]],
    max_rows: int | None = None,
    max_credits: int | None = None,
) -> dict[str, object]:
    skip_counts = Counter(
        row.get("firecrawl_skip_reason", "")
        for row in queue_rows
        if row.get("firecrawl_queue_status") != "queued"
    )
    return {
        "firecrawl_mode": mode,
        "firecrawl_queue_rows": len(queue_rows),
        "firecrawl_queued": sum(1 for row in queue_rows if row.get("firecrawl_queue_status") == "queued"),
        "firecrawl_queue_skipped": sum(1 for row in queue_rows if row.get("firecrawl_queue_status") != "queued"),
        "firecrawl_queue_skip_counts": dict(skip_counts),
        "firecrawl_attempted": 0,
        "firecrawl_enriched": 0,
        "firecrawl_failed": 0,
        "firecrawl_credits_used": 0,
        "firecrawl_max_rows": max_rows,
        "firecrawl_credits_cap": max_credits,
        "firecrawl_stopped_reason": None,
    }


def existing_domains(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return {clean(row.get("root_domain")).lower() for row in reader if clean(row.get("root_domain"))}


def scrape_json(client, url: str, schema: dict, prompt: str) -> tuple[dict | None, int]:
    result = client.scrape(
        url=url,
        formats=[{
            "type": "json",
            "schema": schema,
            "prompt": prompt,
        }],
    )
    return getattr(result, "json", None), response_credits(result)


def scrape_with_retries(client, url: str, schema: dict, prompt: str) -> tuple[dict | None, int, str | None]:
    for retry in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            data, credits = scrape_json(client, url, schema, prompt)
            return data, credits, None
        except Exception as exc:
            if is_credit_error(exc):
                return None, 0, "skipped_no_credits"
            if is_rate_limit_error(exc) and retry < MAX_RATE_LIMIT_RETRIES:
                time.sleep((2**retry) + 1)
                continue
            if is_rate_limit_error(exc):
                return None, 0, "skipped_rate_limit"
            return None, 0, f"skipped_api_error: {exc}"
    return None, 0, "skipped_api_error"


def paid_candidate_rows(
    queue_rows: list[dict[str, str]],
    enriched_path: Path,
    failed_path: Path,
    resume: bool,
) -> list[dict[str, str]]:
    completed = set()
    failed = set()
    if resume:
        completed = existing_domains(enriched_path)
        failed = existing_domains(failed_path)
    out = []
    for row in queue_rows:
        if row.get("firecrawl_queue_status") != "queued":
            continue
        domain = clean(row.get("root_domain")).lower()
        if resume and domain in completed:
            continue
        if resume and domain in failed:
            continue
        out.append(row)
    return out


def run_paid_firecrawl(
    profile,
    queue_rows: list[dict[str, str]],
    enriched_path: Path,
    failed_path: Path,
    max_rows: int | None = None,
    max_credits: int | None = None,
    resume: bool = True,
) -> tuple[dict[str, object], list[dict[str, str]], list[dict[str, str]]]:
    summary = queue_summary("paid", queue_rows, max_rows=max_rows, max_credits=max_credits)
    ensure_csv_header(enriched_path, FIRECRAWL_ENRICHED_FIELDS)
    ensure_csv_header(failed_path, FIRECRAWL_ENRICHED_FIELDS)

    if FirecrawlApp is None:
        summary["firecrawl_stopped_reason"] = "missing_firecrawl_package"
        return summary, [], []
    api_key = firecrawl_api_key()
    if not api_key:
        summary["firecrawl_stopped_reason"] = "missing_firecrawl_api_key"
        return summary, [], []

    config = getattr(profile, "FIRECRAWL", {})
    schema = config.get("schema")
    prompt = config.get("prompt")
    paths = list(config.get("paths") or [""])
    max_pages = int(config.get("max_pages") or len(paths))
    estimate_per_page = int(config.get("credit_estimate_per_page") or DEFAULT_CREDIT_ESTIMATE_PER_PAGE)
    if not schema or not prompt:
        summary["firecrawl_stopped_reason"] = "missing_profile_firecrawl_schema"
        return summary, [], []

    client = FirecrawlApp(api_key=api_key)
    enriched_rows: list[dict[str, str]] = []
    failed_rows: list[dict[str, str]] = []
    candidates = paid_candidate_rows(queue_rows, enriched_path, failed_path, resume=resume)
    if max_rows is not None:
        candidates = candidates[:max_rows]

    for row in candidates:
        page_results: list[dict[str, object]] = []
        pages_attempted = 0
        credits_used = 0
        source_pages: list[str] = []
        error = ""
        base_url = normalize_url(row.get("website") or "")
        if not base_url:
            failed = {
                **row,
                "firecrawl_status": "failed",
                "firecrawl_error": "invalid_website",
            }
            append_csv_row(failed_path, failed, FIRECRAWL_ENRICHED_FIELDS)
            failed_rows.append(failed)
            summary["firecrawl_failed"] = int(summary["firecrawl_failed"]) + 1
            continue

        summary["firecrawl_attempted"] = int(summary["firecrawl_attempted"]) + 1
        for path in paths[:max_pages]:
            if max_credits is not None and int(summary["firecrawl_credits_used"]) + estimate_per_page > max_credits:
                summary["firecrawl_stopped_reason"] = "max_credits"
                break
            url = url_for_path(base_url, path)
            pages_attempted += 1
            data, credits, status = scrape_with_retries(client, url, schema, prompt)
            credits_used += credits
            summary["firecrawl_credits_used"] = int(summary["firecrawl_credits_used"]) + credits
            source_pages.append(url)
            if status:
                error = status
                if status == "skipped_no_credits":
                    summary["firecrawl_stopped_reason"] = "skipped_no_credits"
                if status in {"skipped_no_credits", "skipped_rate_limit"} or status.startswith("skipped_api_error"):
                    break
                continue
            if data:
                page_results.append({"url": url, "data": data})
            if hasattr(profile, "firecrawl_should_stop") and profile.firecrawl_should_stop(page_results):
                break

        if summary.get("firecrawl_stopped_reason") == "max_credits" and pages_attempted == 0:
            break

        result_row = {
            **row,
            "firecrawl_credits_used": str(credits_used),
            "firecrawl_pages_attempted": str(pages_attempted),
            "firecrawl_source_pages": " | ".join(source_pages),
            "firecrawl_error": error,
            "firecrawl_json": json.dumps(page_results, ensure_ascii=True),
        }
        if error == "skipped_no_credits" and not page_results:
            summary["firecrawl_stopped_reason"] = "skipped_no_credits"
            break
        if page_results:
            result_row["firecrawl_status"] = "enriched"
            if hasattr(profile, "apply_firecrawl_enrichment"):
                result_row.update(profile.apply_firecrawl_enrichment(row, page_results))
            append_csv_row(enriched_path, result_row, FIRECRAWL_ENRICHED_FIELDS)
            enriched_rows.append(result_row)
            summary["firecrawl_enriched"] = int(summary["firecrawl_enriched"]) + 1
        else:
            result_row["firecrawl_status"] = "failed"
            result_row["firecrawl_error"] = error or "no_structured_data"
            append_csv_row(failed_path, result_row, FIRECRAWL_ENRICHED_FIELDS)
            failed_rows.append(result_row)
            summary["firecrawl_failed"] = int(summary["firecrawl_failed"]) + 1

        if summary.get("firecrawl_stopped_reason") in {"max_credits", "skipped_no_credits"}:
            break

    return summary, enriched_rows, failed_rows
