"""Offline reconciliation of exact-input enrichment and external research."""

import csv
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from .common import atomic_text, domain, norm, read_json, write_csv, write_json
from .extract import EMAIL, PUBLIC_MAIL_HOSTS, category, professional_url
from .input_enrichment import (
    ALLOWED_ROLES,
    ENRICHED_FIELDS,
    GENERIC_LOCALS,
    INPUT_FIELDS,
    _valid_name,
)


EXTERNAL_REQUIRED_FIELDS = {
    "research_key",
    "root_domain",
    "research_status",
    "primary_decision_maker_name",
    "primary_decision_maker_title",
    "primary_role_category",
    "primary_evidence_status",
    "primary_evidence_url",
    "primary_linkedin_url",
    "primary_named_work_email",
    "primary_email_provenance",
    "primary_email_evidence_url",
    "alternate_decision_maker_name",
    "alternate_decision_maker_title",
    "alternate_role_category",
    "alternate_evidence_status",
    "alternate_evidence_url",
    "alternate_linkedin_url",
    "alternate_named_work_email",
    "alternate_email_provenance",
    "alternate_email_evidence_url",
}

FINAL_EXTRA_FIELDS = [
    "primary_email_evidence_url",
    "alternate_email_evidence_url",
    "external_research_sources",
    "external_qa_flags",
]

FORBIDDEN_SUPERGROK_HOSTS = {
    "linkedin.com",
    "zoominfo.com",
    "datanyze.com",
    "rocketreach.co",
    "apollo.io",
    "facebook.com",
    "instagram.com",
    "alignable.com",
    "buildzoom.com",
    "thebluebook.com",
    "buzzfile.com",
    "bizprofile.net",
    "b2bhint.com",
    "company-detail.com",
    "georgiacompanyregistry.com",
    "opencorpdata.com",
    "nybizdb.com",
}


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or [])
        if not EXTERNAL_REQUIRED_FIELDS <= fields:
            missing = sorted(EXTERNAL_REQUIRED_FIELDS - fields)
            raise ValueError("External result CSV is missing fields: " + ", ".join(missing))
        rows = list(reader)
    keys = [row.get("research_key", "") for row in rows]
    if not rows or any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("External results require unique, nonempty research_key values")
    return rows


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _host(value):
    try:
        return (urlparse(str(value or "")).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _on_host_list(value, hosts):
    host = _host(value)
    return any(host == item or host.endswith("." + item) for item in hosts)


def _person_tokens(value):
    stop = {
        "dr",
        "mr",
        "mrs",
        "ms",
        "jr",
        "sr",
        "ii",
        "iii",
        "iv",
        "pharm",
        "d",
        "pharmd",
        "md",
        "mba",
    }
    return [token for token in norm(value).split() if token not in stop]


def _person_match(left, right):
    a = _person_tokens(left)
    b = _person_tokens(right)
    if not a or not b:
        return False
    return (
        "".join(a) == "".join(b)
        or (a[0] == b[0] and a[-1] == b[-1])
        or (a[-1] == b[-1] and any(token in b and len(token) > 2 for token in a))
    )


def _valid_email(address, person, root, evidence_url, provenance):
    address = str(address or "").strip().lower()
    if not address or EMAIL.fullmatch(address) is None or not evidence_url:
        return ""
    local, _, host = address.partition("@")
    if local in GENERIC_LOCALS:
        return ""
    address_root = domain(host)
    official_evidence = bool(root and domain(evidence_url) == root)
    if address_root != root and not official_evidence:
        return ""
    if host in PUBLIC_MAIL_HOSTS and not official_evidence:
        return ""
    if "inferred" in str(provenance or "").lower():
        return ""
    return address if _valid_name(person) else ""


def _external_candidate(row, provider, prefix):
    name = str(row.get(prefix + "_decision_maker_name") or "").strip()
    title = str(row.get(prefix + "_decision_maker_title") or "").strip()
    role = str(row.get(prefix + "_role_category") or "").strip() or category(title)
    evidence_url = str(row.get(prefix + "_evidence_url") or "").strip()
    linkedin_url = professional_url(
        str(row.get(prefix + "_linkedin_url") or "").strip()
    ) or professional_url(evidence_url)
    root = domain(row.get("root_domain", ""))
    if (
        not _valid_name(name)
        or role not in ALLOWED_ROLES
        or not evidence_url
        or re.search(r"\b(former|retired|past|previous)\b", title, re.I)
    ):
        return None
    if (
        provider == "SuperGrok"
        and _on_host_list(evidence_url, FORBIDDEN_SUPERGROK_HOSTS)
        and not linkedin_url
    ):
        return None
    if root and domain(evidence_url) == root:
        evidence_status = "official_source_backed"
    elif linkedin_url:
        evidence_status = "linkedin_candidate"
    else:
        evidence_status = "input_hint_unconfirmed"
    email_evidence_url = str(row.get(prefix + "_email_evidence_url") or "").strip()
    email_provenance = str(row.get(prefix + "_email_provenance") or "").strip()
    email = _valid_email(
        row.get(prefix + "_named_work_email"),
        name,
        root,
        email_evidence_url,
        email_provenance,
    )
    return {
        "name": name,
        "title": title,
        "role": role,
        "evidence_status": evidence_status,
        "evidence_url": evidence_url,
        "linkedin_url": linkedin_url,
        "email": email,
        "email_provenance": (
            provider.lower() + "_" + email_provenance if email else ""
        ),
        "email_evidence_url": email_evidence_url if email else "",
        "source": provider,
        "slot": prefix,
        "cross_platform": False,
    }


def _canonical_candidates(row):
    output = []
    for prefix in ("primary", "alternate"):
        name = str(row.get(prefix + "_decision_maker_name") or "").strip()
        status = str(row.get(prefix + "_evidence_status") or "").strip()
        role = str(row.get(prefix + "_role_category") or "").strip()
        if not name or status not in {
            "official_source_backed",
            "linkedin_candidate",
            "input_hint_unconfirmed",
        }:
            continue
        output.append(
            {
                "name": name,
                "title": row.get(prefix + "_decision_maker_title", ""),
                "role": role,
                "evidence_status": status,
                "evidence_url": row.get(prefix + "_evidence_url", ""),
                "linkedin_url": row.get(prefix + "_linkedin_url", ""),
                "email": row.get(prefix + "_selected_email", ""),
                "email_provenance": row.get(prefix + "_email_provenance", ""),
                "email_evidence_url": "",
                "source": "Firecrawl",
                "slot": prefix,
                "cross_platform": False,
            }
        )
    return output


def _external_for_key(perplexity, supergrok):
    output = []
    rejected = Counter()
    by_provider = {"Perplexity": perplexity, "SuperGrok": supergrok}
    for provider, row in by_provider.items():
        for prefix in ("primary", "alternate"):
            if not row.get(prefix + "_decision_maker_name"):
                continue
            candidate = _external_candidate(row, provider, prefix)
            if candidate is None:
                rejected[provider + "_candidate_rejected"] += 1
            else:
                output.append(candidate)
    for candidate in output:
        candidate["cross_platform"] = any(
            other["source"] != candidate["source"]
            and _person_match(other["name"], candidate["name"])
            for other in output
        )
    p_names = [
        perplexity.get(prefix + "_decision_maker_name", "")
        for prefix in ("primary", "alternate")
        if perplexity.get(prefix + "_decision_maker_name")
    ]
    s_names = [
        supergrok.get(prefix + "_decision_maker_name", "")
        for prefix in ("primary", "alternate")
        if supergrok.get(prefix + "_decision_maker_name")
    ]
    p_primary = perplexity.get("primary_decision_maker_name", "")
    s_primary = supergrok.get("primary_decision_maker_name", "")
    agreement = any(_person_match(left, right) for left in p_names for right in s_names)
    conflict = bool(
        p_primary
        and s_primary
        and not _person_match(p_primary, s_primary)
        and not agreement
    )
    return output, rejected, conflict


def _rank(candidate, role_priority):
    status = candidate["evidence_status"]
    source = candidate["source"]
    if source == "Firecrawl" and status == "official_source_backed":
        bucket = 0
    elif status == "official_source_backed":
        bucket = 1
    elif candidate.get("cross_platform"):
        bucket = 2
    elif source == "Firecrawl" and status == "linkedin_candidate":
        bucket = 3
    elif status == "linkedin_candidate":
        bucket = 4
    elif source != "Firecrawl":
        bucket = 5
    else:
        bucket = 6
    role = candidate.get("role")
    role_rank = role_priority.index(role) if role in role_priority else len(role_priority)
    return bucket, role_rank, norm(candidate.get("name"))


def _select_candidates(base_row, external, conflict, role_priority):
    canonical = _canonical_candidates(base_row)
    flags = []
    canonical_credible = any(
        item["evidence_status"] in {"official_source_backed", "linkedin_candidate"}
        for item in canonical
    )
    if conflict:
        flags.append("external_candidate_conflict")
        if not canonical_credible:
            official_people = []
            for candidate in external:
                if candidate["evidence_status"] != "official_source_backed":
                    continue
                if not any(_person_match(candidate["name"], name) for name in official_people):
                    official_people.append(candidate["name"])
            if len(official_people) == 1:
                external = [
                    item
                    for item in external
                    if _person_match(item["name"], official_people[0])
                ]
                flags.append("conflict_resolved_by_unique_official_source")
            else:
                external = []
                flags.append("conflict_held_unresolved")
    candidates = sorted(canonical + external, key=lambda item: _rank(item, role_priority))
    selected = []
    for candidate in candidates:
        existing = next(
            (item for item in selected if _person_match(item["name"], candidate["name"])),
            None,
        )
        if existing:
            if not existing.get("email") and candidate.get("email"):
                for field in ("email", "email_provenance", "email_evidence_url"):
                    existing[field] = candidate.get(field, "")
            continue
        selected.append(dict(candidate))
        if len(selected) == 2:
            break
    return selected, flags


def _row_key(row):
    root = domain(row.get("root_domain") or row.get("website"))
    return root or "missing:" + row["research_id"]


def _summary_by_niche(rows):
    output = {}
    for niche, niche_rows in rows.items():
        counts = Counter(row["research_outcome"] for row in niche_rows)
        output[niche] = {
            "rows": len(niche_rows),
            "decision_makers_found": counts["official_source_backed"]
            + counts["linkedin_candidate"],
            "official_source_backed": counts["official_source_backed"],
            "linkedin_candidates": counts["linkedin_candidate"],
            "unconfirmed_candidates": counts["input_hint_unconfirmed"],
            "decision_makers_missing": counts["not_found"],
            "rows_with_named_emails": sum(
                bool(row["primary_selected_email"] or row["alternate_selected_email"])
                for row in niche_rows
            ),
            "generic_only_email_rows": sum(
                not (row["primary_selected_email"] or row["alternate_selected_email"])
                and bool(
                    row["generic_or_role_emails"]
                    or row["free_provider_emails"]
                    or row["unclear_or_held_emails"]
                )
                for row in niche_rows
            ),
            "no_email_rows": sum(
                not (row["primary_selected_email"] or row["alternate_selected_email"])
                and not (
                    row["generic_or_role_emails"]
                    or row["free_provider_emails"]
                    or row["unclear_or_held_emails"]
                    or row["name_email_candidates"]
                )
                for row in niche_rows
            ),
        }
    return output


def finalize_input_results(run_dir, perplexity_path, supergrok_path, output_directory):
    run = Path(run_dir).resolve()
    output = Path(output_directory).resolve()
    campaign = read_json(run / "campaign.json")
    state = read_json(run / "state.json")
    manifest = read_json(run / "manifest.json")
    validation = read_json(run / "validation_report.json", {})
    if not campaign or not state or not manifest:
        raise ValueError("Input run is missing campaign, state, or manifest")
    if validation and not validation.get("valid"):
        raise ValueError("Input run validation is not valid")

    perplexity_rows = _read_csv(perplexity_path)
    supergrok_rows = _read_csv(supergrok_path)
    p_by_key = {row["research_key"]: row for row in perplexity_rows}
    s_by_key = {row["research_key"]: row for row in supergrok_rows}
    if set(p_by_key) != set(s_by_key):
        raise ValueError("Perplexity and SuperGrok research_key sets differ")
    unknown = set(p_by_key) - set(state["jobs"])
    if unknown:
        raise ValueError("External results contain unknown research keys")

    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "external_inputs"
    snapshot.mkdir(parents=True, exist_ok=True)
    p_copy = snapshot / "perplexity_decision_maker_retry_330.csv"
    s_copy = snapshot / "supergrok_decision_maker_retry_330.csv"
    shutil.copy2(perplexity_path, p_copy)
    shutil.copy2(supergrok_path, s_copy)
    write_json(
        snapshot / "source_hashes.json",
        {
            "perplexity": {"path": str(p_copy), "sha256": _sha256(p_copy)},
            "supergrok": {"path": str(s_copy), "sha256": _sha256(s_copy)},
        },
    )

    external_by_key = {}
    conflict_keys = set()
    rejected = Counter()
    for key in p_by_key:
        candidates, current_rejected, conflict = _external_for_key(
            p_by_key[key], s_by_key[key]
        )
        external_by_key[key] = candidates
        rejected.update(current_rejected)
        if conflict:
            conflict_keys.add(key)

    rows_by_niche = defaultdict(list)
    output_ids = []
    external_used = Counter()
    for spec in campaign["inputs"]:
        source_path = run / "inputs" / Path(spec["path"]).name
        base_path = run / spec["output_filename"]
        with source_path.open(encoding="utf-8-sig", newline="") as stream:
            expected_ids = [row["research_id"] for row in csv.DictReader(stream)]
        with base_path.open(encoding="utf-8-sig", newline="") as stream:
            base_rows = list(csv.DictReader(stream))
        if [row["research_id"] for row in base_rows] != expected_ids:
            raise ValueError("Base enriched output does not preserve source row order")
        finalized = []
        for row in base_rows:
            result = dict(row)
            key = _row_key(result)
            external = external_by_key.get(key, [])
            selected, flags = _select_candidates(
                result,
                external,
                key in conflict_keys,
                spec["decision_maker_roles"],
            )
            for prefix in ("primary", "alternate"):
                for suffix in (
                    "decision_maker_name",
                    "decision_maker_title",
                    "role_category",
                    "evidence_status",
                    "evidence_url",
                    "linkedin_url",
                    "selected_email",
                    "email_provenance",
                    "email_evidence_url",
                ):
                    result[prefix + "_" + suffix] = ""
            for prefix, person in zip(("primary", "alternate"), selected):
                result[prefix + "_decision_maker_name"] = person["name"]
                result[prefix + "_decision_maker_title"] = person["title"]
                result[prefix + "_role_category"] = person["role"]
                result[prefix + "_evidence_status"] = person["evidence_status"]
                result[prefix + "_evidence_url"] = person["evidence_url"]
                result[prefix + "_linkedin_url"] = person["linkedin_url"]
                result[prefix + "_selected_email"] = person["email"]
                result[prefix + "_email_provenance"] = person["email_provenance"]
                result[prefix + "_email_evidence_url"] = person["email_evidence_url"]
                if person["source"] != "Firecrawl":
                    external_used[person["source"]] += 1
            status = selected[0]["evidence_status"] if selected else "not_found"
            result["research_outcome"] = status
            gaps = []
            if status == "not_found":
                gaps.append("decision_maker_not_found")
            elif status == "input_hint_unconfirmed":
                gaps.append("decision_maker_unconfirmed")
            if not result["primary_selected_email"]:
                gaps.append("decision_maker_email_not_found")
            gaps.extend(flags)
            result["research_gap"] = " | ".join(dict.fromkeys(gaps))
            providers = sorted({item["source"] for item in external})
            result["external_research_sources"] = " | ".join(providers)
            result["external_qa_flags"] = " | ".join(flags)
            finalized.append(result)
            rows_by_niche[spec["niche"]].append(result)
            output_ids.append(result["research_id"])
        write_csv(
            output / spec["output_filename"],
            finalized,
            INPUT_FIELDS + ENRICHED_FIELDS + FINAL_EXTRA_FIELDS,
        )

    expected_by_spec = []
    for spec in campaign["inputs"]:
        with (run / "inputs" / Path(spec["path"]).name).open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            expected_by_spec.append(sum(1 for _ in csv.DictReader(stream)))
    expected_rows = sum(expected_by_spec)
    if len(output_ids) != expected_rows or len(output_ids) != len(set(output_ids)):
        raise ValueError("Final outputs do not preserve all unique research_id values")

    niche_summary = _summary_by_niche(rows_by_niche)
    execution_history = manifest.get("execution_history") or [
        manifest.get("last_execution_usage", {})
    ]
    execution_history = [value for value in execution_history if value]
    first_usage = execution_history[0] if execution_history else {}
    last_usage = execution_history[-1] if execution_history else {}
    session_accounted_credits = sum(
        int(value.get("accounted_credit_delta", 0)) for value in execution_history
    )
    session_balance_delta = (
        max(
            0,
            int(first_usage.get("starting_balance"))
            - int(last_usage.get("ending_balance")),
        )
        if first_usage.get("starting_balance") is not None
        and last_usage.get("ending_balance") is not None
        else 0
    )
    session_http_requests = sum(
        int(value.get("ending_http_requests", 0))
        - int(value.get("starting_http_requests", 0))
        for value in execution_history
    )
    session_scrapes = sum(
        int(value.get("ending_scrapes", 0))
        - int(value.get("starting_scrapes", 0))
        for value in execution_history
    )
    remaining_untouched = sum(
        research.get("search_status") == "not_started"
        and not research.get("official_contacts")
        for research in state["research"].values()
    )
    retryable_failures = sum(
        research.get("search_status") == "failed"
        and research.get("search_http_status") in {429, 502, 503, 504}
        for research in state["research"].values()
    )
    remaining_search_jobs = remaining_untouched + retryable_failures
    total_successful_searches = sum(
        research.get("search_status") == "success"
        for research in state["research"].values()
    )
    total_accounted_credits = sum(
        int(research.get("credits", 0))
        for research in state["research"].values()
    )
    ending_balance = last_usage.get("ending_balance")
    spendable_balance = max(
        0,
        int(ending_balance or 0) - int(campaign["firecrawl_safety_reserve"]),
    )
    final_validation = {
        "valid": True,
        "expected_rows": expected_by_spec,
        "output_rows": [len(rows_by_niche[spec["niche"]]) for spec in campaign["inputs"]],
        "unique_research_ids": len(set(output_ids)),
        "maps_requests": len(list((run / "receipts" / "maps").glob("*.json"))),
        "last_execution_http_requests": session_http_requests,
        "last_execution_scrapes": session_scrapes,
        "last_execution_searches": max(
            0, (session_accounted_credits - session_scrapes) // 2
        ),
        "last_execution_accounted_credits": session_accounted_credits,
        "last_execution_balance_delta": session_balance_delta,
        "ending_firecrawl_balance": ending_balance,
        "total_successful_firecrawl_searches": total_successful_searches,
        "total_firecrawl_credits_accounted": total_accounted_credits,
        "remaining_untouched_jobs": remaining_untouched,
        "retryable_search_failures": retryable_failures,
        "remaining_search_jobs": remaining_search_jobs,
        "minimum_additional_credits_required": max(
            0, remaining_search_jobs * 2 - spendable_balance
        ),
        "external_conflicts": len(conflict_keys),
        "external_candidates_selected": dict(external_used),
        "external_candidates_rejected": dict(rejected),
        "by_niche": niche_summary,
    }
    if final_validation["output_rows"] != final_validation["expected_rows"]:
        final_validation["valid"] = False
    if final_validation["maps_requests"] != 0:
        final_validation["valid"] = False
    if final_validation["last_execution_http_requests"] != 0:
        final_validation["valid"] = False
    if final_validation["last_execution_scrapes"] != 0:
        final_validation["valid"] = False
    if final_validation["last_execution_accounted_credits"] > 800:
        final_validation["valid"] = False
    if (
        final_validation["ending_firecrawl_balance"] is not None
        and final_validation["ending_firecrawl_balance"] < campaign["firecrawl_safety_reserve"]
    ):
        final_validation["valid"] = False
    write_json(snapshot / "validation_report.json", final_validation)

    lines = ["# Decision-maker enrichment final summary", ""]
    for niche, values in niche_summary.items():
        lines.append(
            f"{niche}: {values['rows']} rows; {values['decision_makers_found']} credible decision makers "
            f"({values['official_source_backed']} official, {values['linkedin_candidates']} LinkedIn); "
            f"{values['unconfirmed_candidates']} unconfirmed; {values['decision_makers_missing']} missing; "
            f"{values['rows_with_named_emails']} rows with named emails."
        )
    lines.extend(
        [
            "",
            f"New Firecrawl Searches: {final_validation['last_execution_searches']}.",
            f"Firecrawl credits accounted this execution: {final_validation['last_execution_accounted_credits']}.",
            f"Firecrawl balance delta this execution: {final_validation['last_execution_balance_delta']}.",
            f"Ending Firecrawl balance: {final_validation['ending_firecrawl_balance']}.",
            f"Total successful Firecrawl Searches in the preserved campaign lineage: {total_successful_searches}.",
            f"Total Firecrawl credits accounted in the preserved campaign lineage: {total_accounted_credits}.",
            "Maps requests this execution: 0.",
            f"Direct HTTP requests this execution: {final_validation['last_execution_http_requests']}.",
            f"Firecrawl fallback scrapes this execution: {final_validation['last_execution_scrapes']}.",
            f"Untouched jobs remaining: {remaining_untouched}.",
            f"Retryable Firecrawl Search failures remaining: {retryable_failures}.",
            f"Total Firecrawl Search jobs remaining: {remaining_search_jobs}.",
            f"Minimum additional Firecrawl credits needed above the protected reserve: {final_validation['minimum_additional_credits_required']}.",
            f"External apparent conflicts: {len(conflict_keys)}.",
            "External result files were reconciled offline; generic inboxes were not selected.",
            "All selected emails remain mailbox-unverified.",
        ]
    )
    atomic_text(output / "run_summary.md", "\n".join(lines) + "\n")
    return {"output_directory": str(output), **final_validation}
