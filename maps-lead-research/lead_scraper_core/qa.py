"""QA report generation for profile-based scraper runs."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from lead_scraper_core.suppression import SuppressionData


def write_qa(
    path: Path,
    profile,
    targets: list,
    raw_rows: list[dict[str, str]],
    campaign_targets: list[dict[str, str]],
    manual_review: list[dict[str, str]],
    geo_rejected: list[dict[str, str]],
    suppressed: list[dict[str, str]],
    suppression: SuppressionData,
    campaign_before_suppression: int,
    manual_before_suppression: int,
    maps_calls: int,
    queried_zips: set[str],
    command: str,
    firecrawl_summary: dict[str, object] | None = None,
    found_registry=None,
    scraped_geo_registry=None,
    already_found: list[dict[str, str]] | None = None,
    final_summary: dict[str, object] | None = None,
    scraped_geo_rows: list[dict[str, str]] | None = None,
    skipped_geo_targets: list | None = None,
    registry_append_summary: dict[str, object] | None = None,
) -> None:
    geo_counts = Counter(row["geo_status"] for row in raw_rows)
    fit_counts = Counter(row["fit_status"] for row in raw_rows)
    label_counts = Counter(row.get("fit_label", "") for row in raw_rows if row.get("fit_label"))
    source_zip_counts = Counter(row["source_zip"] for row in raw_rows)
    rejected_reasons = Counter(row["geo_rejection_reason"] for row in geo_rejected if row.get("geo_rejection_reason"))
    suppression_counts = Counter(row.get("matched_suppression_field", "") for row in suppressed if row.get("matched_suppression_field"))
    firecrawl_summary = firecrawl_summary or {}
    firecrawl_skip_counts = firecrawl_summary.get("firecrawl_queue_skip_counts") or {}
    found_registry = found_registry or object()
    scraped_geo_registry = scraped_geo_registry or object()
    already_found = already_found or []
    final_summary = final_summary or {}
    scraped_geo_rows = scraped_geo_rows or []
    skipped_geo_targets = skipped_geo_targets or []
    registry_append_summary = registry_append_summary or {}

    lines = [
        f"# {profile.DISPLAY_NAME} Maps Smoke QA",
        "",
        f"- Run created: {datetime.now().isoformat(timespec='seconds')}",
        f"- Niche: {profile.NICHE_ID}",
        f"- Command: `{command}`",
        f"- Firecrawl mode: {firecrawl_summary.get('firecrawl_mode', 'off')}",
        f"- Firecrawl queue rows: {firecrawl_summary.get('firecrawl_queue_rows', 0)}",
        f"- Firecrawl queued: {firecrawl_summary.get('firecrawl_queued', 0)}",
        f"- Firecrawl queue skipped: {firecrawl_summary.get('firecrawl_queue_skipped', 0)}",
        f"- Firecrawl attempted: {firecrawl_summary.get('firecrawl_attempted', 0)}",
        f"- Firecrawl enriched: {firecrawl_summary.get('firecrawl_enriched', 0)}",
        f"- Firecrawl failed: {firecrawl_summary.get('firecrawl_failed', 0)}",
        f"- Firecrawl credits used: {firecrawl_summary.get('firecrawl_credits_used', 0)}",
        f"- Firecrawl max rows: {firecrawl_summary.get('firecrawl_max_rows')}",
        f"- Firecrawl credit cap: {firecrawl_summary.get('firecrawl_credits_cap')}",
        f"- Firecrawl stopped reason: {firecrawl_summary.get('firecrawl_stopped_reason')}",
        f"- Target rows configured: {len(targets)}",
        f"- Maps page calls: {maps_calls}",
        f"- Raw rows found: {len(raw_rows)}",
        f"- In-geo rows: {sum(geo_counts[status] for status in ['in_target_zip', 'nearby_target_metro', 'in_target_state'])}",
        f"- Out-of-geo rows: {geo_counts['out_of_geo']}",
        f"- Unknown-geo rows: {geo_counts['unknown_geo']}",
        f"- Campaign targets: {len(campaign_targets)}",
        f"- Manual review rows: {len(manual_review)}",
        f"- Current-run geography rejected rows: {len(geo_rejected)}",
        f"- Suppressed rows: {len(suppressed)}",
        f"- ZIPs searched: {', '.join(sorted(queried_zips))}",
        f"- Suppression master loaded: {'yes' if suppression.loaded else 'no'}",
        f"- Suppression master row count: {suppression.row_count}",
        f"- Unique suppression domains loaded: {suppression.domain_count}",
        f"- Unique suppression company names loaded: {suppression.company_name_count}",
        f"- Unique suppression emails loaded: {suppression.email_count}",
        f"- Unique suppression phones loaded: {suppression.phone_count}",
        f"- Raw rows suppressed: {len(suppressed)}",
        f"- Found-leads registry loaded: {'yes' if getattr(found_registry, 'loaded', False) else 'no'}",
        f"- Found-leads registry row count: {getattr(found_registry, 'row_count', 0)}",
        f"- Unique found place IDs loaded: {getattr(found_registry, 'place_id_count', 0)}",
        f"- Unique found domains loaded: {getattr(found_registry, 'domain_count', 0)}",
        f"- Unique found company names loaded: {getattr(found_registry, 'company_name_count', 0)}",
        f"- Unique found emails loaded: {getattr(found_registry, 'email_count', 0)}",
        f"- Unique found phones loaded: {getattr(found_registry, 'phone_count', 0)}",
        f"- Already-found rows: {len(already_found)}",
        f"- Scraped-geo registry loaded: {'yes' if getattr(scraped_geo_registry, 'loaded', False) else 'no'}",
        f"- Scraped-geo registry row count: {getattr(scraped_geo_registry, 'row_count', 0)}",
        f"- Completed scraped-geo keys loaded: {getattr(scraped_geo_registry, 'completed_count', 0)}",
        f"- Scraped-geo target rows skipped: {len(skipped_geo_targets)}",
        f"- Run-local scraped geos recorded: {len(scraped_geo_rows)}",
        f"- Final enriched leads: {final_summary.get('final_enriched_leads', 0)}",
        f"- Final unenriched targets: {final_summary.get('final_unenriched_targets', 0)}",
        f"- Excluded after enrichment: {final_summary.get('excluded_after_enrichment', 0)}",
        f"- Found registry update rows: {final_summary.get('found_registry_updates', 0)}",
        f"- Found registry rows appended: {registry_append_summary.get('found_registry_appended', 0)}",
        f"- Scraped geo rows appended: {registry_append_summary.get('scraped_geo_appended', 0)}",
        f"- Campaign targets before suppression: {campaign_before_suppression}",
        f"- Campaign targets after suppression: {len(campaign_targets)}",
        f"- Manual review rows before suppression: {manual_before_suppression}",
        f"- Manual review rows after suppression: {len(manual_review)}",
        "",
        "## Geo Status Counts",
        "",
    ]
    lines.extend(f"- {status}: {count}" for status, count in geo_counts.most_common())
    lines.extend(["", "## Fit Status Counts", ""])
    lines.extend(f"- {status}: {count}" for status, count in fit_counts.most_common())
    lines.extend(["", "## Fit Label Counts", ""])
    lines.extend(f"- {label}: {count}" for label, count in label_counts.most_common() or [("None", 0)])
    lines.extend(["", "## Rows By Source ZIP", ""])
    lines.extend(f"- {zip_code}: {count}" for zip_code, count in sorted(source_zip_counts.items()))
    lines.extend(["", "## Top Geo Rejection Reasons", ""])
    lines.extend(f"- {reason}: {count}" for reason, count in rejected_reasons.most_common() or [("None", 0)])
    lines.extend(["", "## Suppression Match Counts", ""])
    lines.extend(f"- {field}: {count}" for field, count in suppression_counts.most_common() or [("None", 0)])
    lines.extend(["", "## Firecrawl Queue Skip Counts", ""])
    if isinstance(firecrawl_skip_counts, dict) and firecrawl_skip_counts:
        lines.extend(f"- {reason}: {count}" for reason, count in sorted(firecrawl_skip_counts.items()))
    else:
        lines.append("- None: 0")
    lines.extend(["", "## Notes", ""])
    lines.append(
        "- Geography validation uses parsed address/city/state/ZIP plus RapidAPI latitude/longitude when available. "
        "Rows outside the target state are rejected and do not become campaign targets."
    )
    lines.append(
        "- Firecrawl dry-run mode writes the deterministic usefulness queue and uses 0 credits. "
        "Paid mode processes the same queued rows after geo, dedupe, suppression, and fit filtering."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
