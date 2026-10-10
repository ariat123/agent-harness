"""Stable v2 CSV contracts and exhaustive observation reconciliation."""

from collections import Counter
from pathlib import Path
from .geography import STATE_REGION

from .common import atomic_text, read_json, stable_id, write_csv, write_json

LOCATION_FIELDS = [
    "location_id",
    "company_id",
    "place_id",
    "company_name",
    "website",
    "root_domain",
    "phone",
    "address",
    "street_address",
    "city",
    "state",
    "zip_code",
    "country",
    "category",
    "rating",
    "review_count",
    "business_status",
    "google_maps_url",
    "latitude",
    "longitude",
    "query_used",
    "search_location",
    "timestamp",
    "raw_response_path",
]
OBS_FIELDS = [
    "observation_id",
    "disposition",
    "reason",
    "matched_by",
    "source_index",
] + LOCATION_FIELDS
SCHEMAS = {
    "companies.csv": [
        "company_id",
        "company_name",
        "root_domain",
        "website",
        "phone",
        "first_seen",
        "last_seen",
        "research_status",
        "source_relevance",
        "summary",
        "service_mix",
        "service_evidence",
    ],
    "locations.csv": LOCATION_FIELDS,
    "company_campaign_memberships.csv": [
        "membership_id",
        "company_id",
        "campaign",
        "run_id",
        "disposition",
        "first_seen",
    ],
    "people_contacts.csv": [
        "contact_id",
        "company_id",
        "name",
        "exact_title",
        "role_category",
        "location_id",
        "associated_location",
        "current_role_source_url",
        "team_bio_url",
        "professional_profile_url",
        "public_work_email",
        "public_business_phone",
        "confidence",
        "evidence_date",
        "evidence_excerpt",
    ],
    "email_candidates.csv": [
        "email_id",
        "company_id",
        "contact_id",
        "email",
        "status",
        "provenance",
        "deliverability",
        "source_url",
        "pattern_evidence",
        "evidence_date",
    ],
    "already_found.csv": OBS_FIELDS,
    "objective_exclusions.csv": OBS_FIELDS,
    "duplicates.csv": OBS_FIELDS,
    "discovery_observations.csv": OBS_FIELDS,
    "research_gaps.csv": ["company_id", "root_domain", "gap", "detail"],
    "firecrawl_backlog.csv": [
        "company_id",
        "root_domain",
        "reason",
        "pages_completed",
        "operations_attempted",
    ],
    "email_verification_upload.csv": [
        "email",
        "company_id",
        "contact_id",
        "provenance",
        "source_url",
    ],
    "provider_usage_ledger.csv": [
        "provider",
        "request_id",
        "status",
        "requested_at",
        "raw_path",
        "http_status",
        "accounted_credits",
        "provider_reported_credits",
        "balance_before",
        "balance_after",
    ],
}
EMAIL_STATUSES = {
    "public_named_email",
    "public_general_email",
    "inferred_pattern",
    "catch_all_domain",
    "unverified",
    "not_found",
}


def export(run, reg):
    c = read_json(run / "campaign.json")
    manifest = read_json(run / "manifest.json")
    state = read_json(run / "state.json")
    observations = state["observations"]
    ids = {o["company_id"] for o in observations if o["disposition"] == "included"}
    queue = {j["domain"]: j for j in state.get("queue", [])}
    # An explicit backlog run exports its resumed companies, separately labeled.
    for d, r in state.get("research", {}).items():
        if d in queue and not r.get("reused"):
            ids.add(queue[d]["company_id"])
    tables = {name: [] for name in SCHEMAS}
    people = []
    emails = []
    gaps = []
    backlog = []
    for cid in sorted(ids):
        co = state.get("company_snapshots", {}).get(cid) or reg.company(cid)
        d = co["root_domain"]
        r = state.get("research", {}).get(d, {})
        e = r.get("evidence", {})
        tables["companies.csv"].append(
            {
                **co["data"],
                **{k: v for k, v in co.items() if k != "data"},
                "research_status": r.get("status", "pending"),
                "source_relevance": e.get("source_relevance", "needs_review"),
                "summary": e.get("summary", ""),
                "service_mix": e.get("service_mix", []),
                "service_evidence": e.get("service_evidence", []),
            }
        )
        people += e.get("contacts", [])
        emails += e.get("emails", [])
        if not d:
            gaps.append(
                {
                    "company_id": cid,
                    "root_domain": d,
                    "gap": "no_usable_domain",
                    "detail": "Website research unavailable",
                }
            )
        if not e.get("contacts"):
            gaps.append(
                {
                    "company_id": cid,
                    "root_domain": d,
                    "gap": "current_decision_maker_not_found",
                    "detail": "No supported current-role evidence",
                }
            )
        if not any(x.get("email") for x in e.get("emails", [])):
            gaps.append(
                {
                    "company_id": cid,
                    "root_domain": d,
                    "gap": "email_not_found",
                    "detail": "No qualifying public email or supported company pattern",
                }
            )
        if e.get("source_relevance") != "confirmed":
            gaps.append(
                {
                    "company_id": cid,
                    "root_domain": d,
                    "gap": "source_relevance_needs_review",
                    "detail": "Inspect official service evidence; no ICP exclusion applied",
                }
            )
        if d and r.get("status") != "complete":
            backlog.append(
                {
                    "company_id": cid,
                    "root_domain": d,
                    "reason": r.get("reason", "not_started"),
                    "pages_completed": len(e.get("pages", [])),
                    "operations_attempted": r.get("firecrawl_attempts", 0),
                }
            )
    seen = set()
    for o in observations:
        if (
            o.get("company_id") in ids
            and o.get("location_id")
            and o["location_id"] not in seen
        ):
            tables["locations.csv"].append(o)
            seen.add(o["location_id"])
    for cid in sorted(ids):
        if not any(
            location["company_id"] == cid for location in tables["locations.csv"]
        ):
            for loc in reg.rows("SELECT * FROM locations WHERE company_id=?", (cid,)):
                import json

                tables["locations.csv"].append(
                    {
                        **json.loads(loc["data"]),
                        "location_id": loc["location_id"],
                        "company_id": cid,
                    }
                )
    membership_ids = ids | {
        o["company_id"] for o in observations if o.get("company_id")
    }
    for cid in sorted(membership_ids):
        disposition = (
            "net_new"
            if any(
                o.get("company_id") == cid and o["disposition"] == "included"
                for o in observations
            )
            else "backlog_research"
            if cid in ids
            else "historical_or_excluded"
        )
        tables["company_campaign_memberships.csv"].append(
            {
                "membership_id": stable_id("membership", manifest["run_id"], cid),
                "company_id": cid,
                "campaign": c["campaign"],
                "run_id": manifest["run_id"],
                "disposition": disposition,
                "first_seen": manifest["started_at"],
            }
        )
    tables["people_contacts.csv"] = people
    tables["email_candidates.csv"] = emails
    for filename, kind in [
        ("already_found.csv", "historical_match"),
        ("objective_exclusions.csv", "objective_exclusion"),
        ("duplicates.csv", "duplicate"),
    ]:
        tables[filename] = [o for o in observations if o["disposition"] == kind]
    tables["discovery_observations.csv"] = observations
    tables["research_gaps.csv"] = gaps
    tables["firecrawl_backlog.csv"] = backlog
    tables["email_verification_upload.csv"] = [e for e in emails if e.get("email")]
    for p in sorted((run / "receipts/maps").glob("*.json")):
        r = read_json(p)
        tables["provider_usage_ledger.csv"].append(
            {
                "provider": "rapidapi_maps",
                "request_id": r["request_id"],
                "status": r["status"],
                "requested_at": r["request"]["requested_at"],
                "raw_path": r["raw_path"],
                "http_status": r.get("http_status", ""),
                "accounted_credits": "",
            }
        )
    usage = read_json(run / "receipts/firecrawl_usage.json", {})
    for r in usage.get("operations", {}).values():
        tables["provider_usage_ledger.csv"].append(
            {"provider": "firecrawl", **r, "request_id": r["id"]}
        )
    counts = Counter(o["disposition"] for o in observations)
    errors = []
    if len({o["observation_id"] for o in observations}) != len(observations):
        errors.append("duplicate_observation_ids")
    if set(counts) - {
        "included",
        "historical_match",
        "objective_exclusion",
        "duplicate",
    }:
        errors.append("unreconciled_disposition")
    if counts["included"] > c["max_net_new_companies"]:
        errors.append("net_new_cap_exceeded")
    from .pipeline import payload_items

    # A continuation reuses the source run's immutable discovery evidence rather
    # than issuing Maps requests again.  Reconcile copied observations to that
    # source evidence while keeping the continuation's own Maps usage at zero.
    evidence_run = run
    if state.get("continuation_source_run"):
        source = Path(state["continuation_source_run"])
        if source.is_dir():
            evidence_run = source
        else:
            errors.append("missing_continuation_source_evidence")
    raw_count = 0
    for path in (evidence_run / "receipts/maps").glob("*.json"):
        receipt = read_json(path)
        if receipt["status"] == "success":
            raw_path = Path(receipt["raw_path"])
            if not raw_path.is_absolute():
                raw_path = evidence_run / raw_path
            raw = read_json(raw_path, {})
            try:
                raw_count += len(payload_items(raw.get("payload")))
            except ValueError:
                errors.append("missing_or_invalid_raw_maps_evidence")
    if raw_count != len(observations):
        errors.append("raw_observation_count_mismatch")
    if usage:
        spent = max(
            sum(
                op.get("accounted_credits", 1)
                for op in usage.get("operations", {}).values()
            ),
            usage["starting_balance"] - usage["ending_balance"],
        )
        if spent > c["max_firecrawl_credit_delta"]:
            errors.append("firecrawl_credit_delta_exceeded")
        if usage["ending_balance"] < c["firecrawl_safety_reserve"]:
            errors.append("firecrawl_reserve_crossed")
    if any(
        n > c["max_contacts_per_company"]
        for n in Counter(p["company_id"] for p in people).values()
    ):
        errors.append("contact_cap_exceeded")
    if any(
        n > c["max_emails_per_company"]
        for n in Counter(e["company_id"] for e in emails).values()
    ):
        errors.append("email_cap_exceeded")
    maps_count = len(list((run / "receipts/maps").glob("*.json")))
    if maps_count > c["max_maps_requests"]:
        errors.append("maps_cap_exceeded")
    if any(
        e.get("status") not in EMAIL_STATUSES or e.get("deliverability") != "unverified"
        for e in emails
    ):
        errors.append("invalid_email_claim")
    contact_ids = {p["contact_id"] for p in people}
    if any(e.get("contact_id") and e["contact_id"] not in contact_ids for e in emails):
        errors.append("orphan_contact_email")
    if any(not p.get("current_role_source_url") for p in people):
        errors.append("missing_role_evidence")
    if any(
        e["status"] == "inferred_pattern" and len(e.get("pattern_evidence", [])) < 2
        for e in emails
    ):
        errors.append("unsupported_email_inference")
    for d, r in state.get("research", {}).items():
        if r.get("firecrawl_attempts", 0) > c["max_firecrawl_pages_per_domain"]:
            errors.append("domain_operations_cap_exceeded")
    for name, rows in tables.items():
        write_csv(run / name, rows, SCHEMAS[name])
    validation = {
        "schema_version": 2,
        "valid": not errors,
        "errors": errors,
        "observed_records": len(observations),
        "dispositions": dict(counts),
        "reconciled_records": sum(counts.values()),
        "net_new_companies": counts["included"],
        "output_counts": {k: len(v) for k, v in tables.items()},
        "maps_request_attempts": maps_count,
        "verification_calls": 0,
        "network_used_for_export": False,
    }
    write_json(run / "validation_report.json", validation)
    coverage = {
        read_json(p)["request"]["target"]["state"]
        for p in (run / "receipts/maps").glob("*.json")
        if read_json(p)["status"] == "success"
    } - {""}
    write_json(
        run / "geography_coverage.json",
        {
            "searched_states": sorted(coverage),
            "searched_regions": sorted({STATE_REGION[s] for s in coverage}),
            "planned_targets": len(read_json(run / "geography_plan.json")["targets"]),
        },
    )
    manifest.update(
        net_new_companies=counts["included"],
        output_company_count=len(ids),
        validation_valid=not errors,
        maps_requests=maps_count,
        provider_request_attempts=maps_count + len(usage.get("operations", {})),
    )
    write_json(run / "manifest.json", manifest)
    atomic_text(
        run / "run_summary.md",
        f"# {c['campaign']}\n\nNet-new companies: {counts['included']} / {c['max_net_new_companies']}. Exported companies including resumed backlog: {len(ids)}.\n\nObserved locations/results: {len(observations)}; dispositions: {dict(counts)}. All observations reconcile: {not errors}.\n\nMaps request attempts: {maps_count} / {c['max_maps_requests']}. Firecrawl usage: {manifest.get('firecrawl_usage', {})}.\n\nResearch backlog: {len(backlog)}. Research gaps: {len(gaps)}. Discovery stop: {manifest.get('discovery_stop_reason', 'not_started')}.\n\nPublic and inferred email candidates remain mailbox-unverified. No outreach or verifier calls are part of this product. Source relevance is separate from downstream ICP qualification.\n",
    )
    return validation
