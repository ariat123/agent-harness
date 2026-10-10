"""Exact-input decision-maker enrichment without Google Maps discovery."""

import csv
import copy
import json
import re
import shutil
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from .common import domain, lock, norm, now, read_json, stable_id, write_csv, write_json
from .extract import EMAIL, NAME, ROLE_PATTERNS, category, extract, professional_url
from .pipeline import http_fetch, page_from_payload
from .providers import ACCOUNT_STATE, AccountBudget, BudgetStop, Firecrawl, ProviderError
from .registry import Registry


INPUT_FIELDS = [
    "research_id",
    "niche",
    "company_name",
    "website",
    "root_domain",
    "city",
    "state",
    "categories",
    "address",
    "google_maps_urls",
    "gap_type",
    "known_decision_makers",
    "name_email_candidates",
    "generic_or_role_emails",
    "free_provider_emails",
    "unclear_or_held_emails",
    "research_goal",
    "source_files",
    "source_rows",
]

ENRICHED_FIELDS = [
    "primary_decision_maker_name",
    "primary_decision_maker_title",
    "primary_role_category",
    "primary_evidence_status",
    "primary_evidence_url",
    "primary_linkedin_url",
    "primary_selected_email",
    "primary_email_provenance",
    "alternate_decision_maker_name",
    "alternate_decision_maker_title",
    "alternate_role_category",
    "alternate_evidence_status",
    "alternate_evidence_url",
    "alternate_linkedin_url",
    "alternate_selected_email",
    "alternate_email_provenance",
    "research_outcome",
    "research_gap",
    "research_reused",
    "http_requests",
    "firecrawl_searches",
    "firecrawl_scrapes",
    "firecrawl_credits_accounted",
]

ALLOWED_ROLES = set(ROLE_PATTERNS)
BAD_NAME_WORDS = {
    "about",
    "contact",
    "learn",
    "management",
    "more",
    "operations",
    "our",
    "portal",
    "pricing",
    "read",
    "referrals",
    "team",
    "view",
}
GENERIC_LOCALS = {
    "admin",
    "contact",
    "customerservice",
    "hello",
    "info",
    "office",
    "sales",
    "service",
    "support",
    "team",
}


def load_input_campaign(path):
    c = json.loads(Path(path).read_text())
    required = {
        "version",
        "campaign",
        "inputs",
        "output_directory",
        "historical_registry_path",
        "max_firecrawl_credit_delta",
        "firecrawl_safety_reserve",
        "search_result_limit",
        "max_firecrawl_searches_per_domain",
        "max_firecrawl_scrapes_per_domain",
        "max_http_pages_per_domain",
        "reconcile_runs",
    }
    if set(c) != required or c.get("version") != 1:
        raise ValueError("Invalid exact-input enrichment campaign fields/version")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", str(c["campaign"])):
        raise ValueError("Invalid campaign slug")
    if not isinstance(c["inputs"], list) or len(c["inputs"]) != 3:
        raise ValueError("Exactly three input definitions are required")
    outputs = set()
    for item in c["inputs"]:
        if set(item) != {"path", "niche", "output_filename", "decision_maker_roles"}:
            raise ValueError("Invalid input definition")
        path = Path(item["path"]).resolve()
        if not path.is_file() or path.suffix.lower() != ".csv":
            raise ValueError("Input CSV does not exist: " + str(path))
        item["path"] = str(path)
        output = Path(item["output_filename"])
        if output.name != str(output) or output.suffix.lower() != ".csv":
            raise ValueError("Output filename must be a CSV basename")
        if output.name in outputs:
            raise ValueError("Output filenames must be unique")
        outputs.add(output.name)
        roles = item["decision_maker_roles"]
        if (
            not isinstance(roles, list)
            or not roles
            or len(set(roles)) != len(roles)
            or set(roles) - ALLOWED_ROLES
        ):
            raise ValueError("Invalid decision-maker role priorities")
    for key in (
        "max_firecrawl_credit_delta",
        "firecrawl_safety_reserve",
        "max_firecrawl_searches_per_domain",
        "max_firecrawl_scrapes_per_domain",
        "max_http_pages_per_domain",
    ):
        if type(c[key]) is not int or c[key] < 0:
            raise ValueError(key + " must be a nonnegative integer")
    if c["max_firecrawl_searches_per_domain"] > 1:
        raise ValueError("At most one Firecrawl Search per domain is supported")
    if c["max_firecrawl_scrapes_per_domain"] > 1:
        raise ValueError("At most one fallback scrape per domain is supported")
    if c["max_http_pages_per_domain"] > 1:
        raise ValueError("At most one HTTP page per domain is supported")
    if type(c["search_result_limit"]) is not int or not 1 <= c["search_result_limit"] <= 10:
        raise ValueError("search_result_limit must be 1..10")
    if not isinstance(c["reconcile_runs"], list):
        raise ValueError("reconcile_runs must be a list")
    c["output_directory"] = str(Path(c["output_directory"]).resolve())
    c["historical_registry_path"] = str(Path(c["historical_registry_path"]).resolve())
    c["reconcile_runs"] = [str(Path(p).resolve()) for p in c["reconcile_runs"]]
    if Path(c["output_directory"]) == Path(c["historical_registry_path"]):
        raise ValueError("Output and registry paths must differ")
    return c


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != INPUT_FIELDS:
            raise ValueError("Unexpected input columns in " + str(path))
        rows = list(reader)
    if not rows or any(not r.get("research_id") for r in rows):
        raise ValueError("Input rows require research_id")
    if len({r["research_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate research_id within " + str(path))
    return rows


def input_plan(c):
    rows = []
    files = []
    for spec in c["inputs"]:
        current = _read_csv(spec["path"])
        files.append(
            {
                "path": spec["path"],
                "niche": spec["niche"],
                "rows": len(current),
                "output_filename": spec["output_filename"],
            }
        )
        rows.extend(current)
    domains = {domain(r["root_domain"] or r["website"]) for r in rows}
    domains.discard("")
    return {
        "valid": True,
        "campaign": c["campaign"],
        "files": files,
        "input_rows": len(rows),
        "unique_domains": len(domains),
        "rows_without_domain": sum(
            not domain(r["root_domain"] or r["website"]) for r in rows
        ),
        "hard_firecrawl_credit_ceiling": c["max_firecrawl_credit_delta"],
        "firecrawl_safety_reserve": c["firecrawl_safety_reserve"],
        "max_searches_per_domain": c["max_firecrawl_searches_per_domain"],
        "max_scrapes_per_domain": c["max_firecrawl_scrapes_per_domain"],
        "max_http_pages_per_domain": c["max_http_pages_per_domain"],
        "maps_requests": 0,
        "network_requests": 0,
    }


def _initial_state(c, run):
    rows = []
    jobs = {}
    seen_ids = set()
    for spec_index, spec in enumerate(c["inputs"]):
        source = Path(spec["path"])
        snapshot = run / "inputs" / source.name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, snapshot)
        for row_index, row in enumerate(_read_csv(snapshot), 1):
            if row["research_id"] in seen_ids:
                raise ValueError("research_id must be unique across all inputs")
            seen_ids.add(row["research_id"])
            d = domain(row["root_domain"] or row["website"])
            key = d or "missing:" + row["research_id"]
            stored = dict(row)
            stored.update(
                _input_index=spec_index,
                _input_row=row_index,
                _job_key=key,
            )
            rows.append(stored)
            job = jobs.setdefault(
                key,
                {
                    "key": key,
                    "domain": d,
                    "company_names": [],
                    "niches": [],
                    "row_ids": [],
                    "city": row["city"],
                    "state": row["state"],
                },
            )
            for field, value in (
                ("company_names", row["company_name"]),
                ("niches", spec["niche"]),
                ("row_ids", row["research_id"]),
            ):
                if value not in job[field]:
                    job[field].append(value)
    return {"rows": rows, "jobs": jobs, "research": {}, "created_at": now()}


def initialize_input_run(c):
    run = Path(c["output_directory"])
    run.mkdir(parents=True, exist_ok=True)
    saved = read_json(run / "campaign.json")
    if saved is not None:
        if saved != c:
            raise ValueError("Run configuration is immutable; use a new output directory")
        return run
    # The CLI acquires this run's lock before initialization; the lock file is
    # coordination state, not campaign content.
    if any(path.name != ".run.lock" for path in run.iterdir()):
        raise ValueError("New run directory must be empty")
    write_json(run / "campaign.json", c)
    write_json(run / "state.json", _initial_state(c, run))
    write_json(
        run / "manifest.json",
        {
            "schema_version": 1,
            "run_id": stable_id("input_run", str(run), c),
            "campaign": c["campaign"],
            "research_status": "not_started",
            "research_stop_reason": "",
            "maps_requests": 0,
            "started_at": now(),
        },
    )
    return run


def initialize_input_successor(source_run, output_directory, max_firecrawl_credit_delta):
    """Create a bounded continuation ledger without mutating its source run.

    The derived research state is copied so successful domains are never resent.
    Provider receipts remain in the source run; the successor records only new
    operations and retains explicit lineage in its manifest.
    """
    source = Path(source_run).resolve()
    output = Path(output_directory).resolve()
    if source == output:
        raise ValueError("Successor output directory must differ from source run")
    if type(max_firecrawl_credit_delta) is not int or max_firecrawl_credit_delta <= 0:
        raise ValueError("Successor Firecrawl credit delta must be positive")
    source_campaign = load_input_campaign(source / "campaign.json")
    source_state = read_json(source / "state.json")
    source_manifest = read_json(source / "manifest.json")
    if not isinstance(source_state, dict) or not isinstance(source_manifest, dict):
        raise ValueError("Source input run is incomplete")
    if any(
        value.get("search_status") == "pending"
        or value.get("scrape_status") == "pending"
        for value in source_state.get("research", {}).values()
    ):
        raise ValueError("Source run has pending provider operations")
    c = copy.deepcopy(source_campaign)
    c["campaign"] = (source_campaign["campaign"] + "-continuation")[:80]
    c["output_directory"] = str(output)
    c["max_firecrawl_credit_delta"] = max_firecrawl_credit_delta
    c["reconcile_runs"] = list(dict.fromkeys(c["reconcile_runs"] + [str(source)]))
    run = initialize_input_run(c)
    inherited = copy.deepcopy(source_state)
    inherited["continued_from_run"] = str(source)
    inherited["continued_at"] = now()
    write_json(run / "state.json", inherited)
    manifest = read_json(run / "manifest.json")
    manifest.update(
        continued_from_run=str(source),
        continued_from_run_id=source_manifest.get("run_id", ""),
        inherited_execution_history=source_manifest.get("execution_history", []),
    )
    write_json(run / "manifest.json", manifest)
    return {"run_dir": str(run), "campaign": c}


def reconcile_not_sent(c, account_path=ACCOUNT_STATE):
    ledger = read_json(account_path, {"runs": {}})
    accounted = {
        str(Path(op.get("raw_path", "")).resolve())
        for value in ledger.get("runs", {}).values()
        for op in value.get("operations", {}).values()
        if op.get("raw_path")
    }
    reconciled = []
    for value in c["reconcile_runs"]:
        run = Path(value)
        state_path = run / "state.json"
        state = read_json(state_path)
        if not isinstance(state, dict):
            continue
        changed = False
        for d, receipt in state.get("research", {}).items():
            for attempt in receipt.get("attempts", []):
                if attempt.get("provider") != "firecrawl" or attempt.get("status") != "pending":
                    continue
                raw = Path(attempt.get("raw_path", ""))
                raw = raw if raw.is_absolute() else run / raw
                if raw.exists() or str(raw.resolve()) in accounted:
                    continue
                attempt.update(
                    status="not_sent",
                    reconciliation="no_raw_response_and_no_account_operation",
                    reconciled_at=now(),
                )
                receipt["firecrawl_attempts"] = sum(
                    a.get("provider") == "firecrawl" and a.get("status") != "not_sent"
                    for a in receipt.get("attempts", [])
                )
                write_json(
                    run / "receipts" / "research" / (stable_id("domain", d) + ".json"),
                    {"job": next((j for j in state.get("queue", []) if j.get("domain") == d), {}), "receipt": receipt},
                )
                reconciled.append({"run": str(run), "domain": d, "raw_path": str(raw)})
                changed = True
        if changed:
            write_json(state_path, state)
            manifest = read_json(run / "manifest.json", {})
            manifest.update(
                research_stop_reason="reconciled_not_sent_placeholder",
                updated_at=now(),
            )
            write_json(run / "manifest.json", manifest)
            write_json(
                run / "receipts" / "firecrawl_reconciliation.json",
                {"reconciled_at": now(), "not_sent": reconciled},
            )
    return reconciled


def _role_config(c, job):
    roles = []
    for niche in job["niches"]:
        spec = next(s for s in c["inputs"] if s["niche"] == niche)
        roles.extend(spec["decision_maker_roles"])
    roles.extend(sorted(ALLOWED_ROLES))
    return {
        "decision_maker_roles": list(dict.fromkeys(roles)),
        "max_contacts_per_company": 100,
        "max_emails_per_company": 100,
        "business_category": "",
    }


def _official_contacts(cid, d, pages, c, job, prior=None):
    evidence = extract(cid, d, pages, _role_config(c, job)) if d and pages else {"contacts": [], "emails": [], "pages": pages}
    contacts = list(evidence.get("contacts", []))
    if prior:
        for person in prior.get("contacts", []):
            if person.get("confidence") == "source_backed" and person.get("current_role_source_url"):
                if not any(norm(x.get("name")) == norm(person.get("name")) for x in contacts):
                    contacts.append(person)
        emails = list(evidence.get("emails", [])) + list(prior.get("emails", []))
    else:
        emails = list(evidence.get("emails", []))
    output = []
    for person in contacts:
        role = person.get("role_category") or category(person.get("exact_title", ""))
        if role not in ALLOWED_ROLES or not _valid_name(person.get("name", "")):
            continue
        selected = person.get("public_work_email", "")
        provenance = "public_source" if selected else ""
        if not selected:
            matches = [
                e
                for e in emails
                if e.get("contact_id") == person.get("contact_id")
                and e.get("email")
                and e.get("status") in {"public_named_email", "inferred_pattern"}
            ]
            if matches:
                selected = matches[0]["email"]
                provenance = matches[0].get("provenance", matches[0].get("status", ""))
        output.append(
            {
                "name": person["name"],
                "title": person.get("exact_title", ""),
                "role": role,
                "evidence_status": "official_source_backed",
                "evidence_url": person.get("current_role_source_url", ""),
                "linkedin_url": person.get("professional_profile_url", ""),
                "email": selected,
                "email_provenance": provenance,
            }
        )
    evidence["contacts"] = contacts
    evidence["emails"] = emails
    return output, evidence


def _valid_name(value):
    text = str(value or "").strip()
    return bool(
        re.fullmatch(NAME, text)
        and not (set(norm(text).split()) & BAD_NAME_WORDS)
        and not re.search(r"\b(former|retired|past|late)\b", text, re.I)
    )


def _hint_contacts(row):
    contacts = []
    for item in str(row.get("known_decision_makers") or "").split("|"):
        m = re.fullmatch(r"\s*(.+?)\s*\((.+?)\)\s*", item)
        if not m or not _valid_name(m.group(1)):
            continue
        role = category(m.group(2))
        if role not in ALLOWED_ROLES:
            continue
        contacts.append(
            {
                "name": m.group(1).strip(),
                "title": m.group(2).strip(),
                "role": role,
                "evidence_status": "input_hint_unconfirmed",
                "evidence_url": "",
                "linkedin_url": "",
                "email": "",
                "email_provenance": "",
            }
        )
    return contacts


def _search_items(payload):
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    items = data.get("web", []) if isinstance(data, dict) else []
    return [x for x in items if isinstance(x, dict) and isinstance(x.get("url"), str)]


def _role_from_text(text):
    if re.search(r"\b(former|retired|past|previous)\b", text, re.I):
        return "", ""
    for role, pattern in ROLE_PATTERNS.items():
        m = re.search(r"\b(" + pattern + r")\b", text, re.I)
        if m:
            return role, m.group(1)
    return "", ""


def _linkedin_candidates(job, items):
    output = []
    company_tokens = [
        t
        for t in norm(job["company_names"][0]).split()
        if len(t) >= 4 and t not in {"company", "group", "services", "management"}
    ]
    for item in items:
        url = professional_url(item.get("url", ""))
        if not url:
            continue
        title = str(item.get("title") or "")
        description = str(item.get("description") or "")
        text = title + " " + description
        if company_tokens and not any(t in norm(text).split() for t in company_tokens):
            continue
        name = re.split(r"\s+(?:[-–—|]|at\b)", title, maxsplit=1)[0].strip()
        role, exact = _role_from_text(text)
        if not _valid_name(name) or not role:
            continue
        output.append(
            {
                "name": name,
                "title": exact,
                "role": role,
                "evidence_status": "linkedin_candidate",
                "evidence_url": url,
                "linkedin_url": url,
                "email": "",
                "email_provenance": "",
            }
        )
    unique = {}
    for item in output:
        unique.setdefault(norm(item["name"]), item)
    return list(unique.values())


def _attach_search_named_emails(job, items, contacts):
    """Attach only explicit named emails from official-domain Search snippets."""
    root = job.get("domain", "")
    if not root or not contacts:
        return contacts
    for item in items:
        url = str(item.get("url") or "")
        if domain(url) != root:
            continue
        text = " ".join(
            str(item.get(field) or "") for field in ("title", "description")
        )
        words = set(norm(text).split())
        people = []
        for person in contacts:
            name_tokens = norm(person.get("name")).split()
            if len(name_tokens) >= 2 and all(token in words for token in name_tokens):
                people.append(person)
        if len(people) != 1:
            continue
        emails = []
        for address in EMAIL.findall(text):
            address = address.strip().lower()
            local, _, host = address.partition("@")
            if not host or local in GENERIC_LOCALS or domain(host) != root:
                continue
            if address not in emails:
                emails.append(address)
        if len(emails) == 1 and not people[0].get("email"):
            people[0]["email"] = emails[0]
            people[0]["email_provenance"] = (
                "firecrawl_search_public_person_email | " + url
            )
    return contacts


def _official_url(job, items):
    root = job.get("domain", "")
    ranked = []
    for item in items:
        url = item.get("url", "")
        d = domain(url)
        if root:
            if d != root:
                continue
        elif not d:
            continue
        text = (url + " " + str(item.get("title") or "") + " " + str(item.get("description") or "")).lower()
        name_tokens = [t for t in norm(job["company_names"][0]).split() if len(t) >= 4]
        if not root and name_tokens and not any(t in norm(text).split() for t in name_tokens):
            continue
        score = 0
        for index, token in enumerate(("leadership", "team", "management", "about", "owner", "president", "contact")):
            if token in text:
                score += 20 - index
        path = urlparse(url).path
        if path in ("", "/"):
            score -= 5
        ranked.append((-score, url))
    return sorted(ranked)[0][1] if ranked else ""


def _query(job):
    place = " ".join(v for v in (job.get("city"), job.get("state")) if v)
    return (
        '"' + job["company_names"][0].replace('"', "") + '" '
        + place
        + " owner founder president CEO principal general manager operations LinkedIn"
    ).strip()


def _compact_search_items(items):
    """Keep derived ranking evidence small; full provider payload stays in raw/."""
    return [
        {
            "url": str(item.get("url") or "")[:4000],
            "title": str(item.get("title") or "")[:1000],
            "description": str(item.get("description") or "")[:3000],
            "position": item.get("position"),
        }
        for item in items
    ]


def _compact_pages(pages):
    """Page bodies are durable in raw files and need not be duplicated in state."""
    return [
        {
            "url": page.get("url", ""),
            "timestamp": page.get("timestamp", ""),
            "raw_path": page.get("raw_path", ""),
        }
        for page in pages
    ]


def _apply_http_result(run, c, state, key, research, result, raw_rel):
    research["http_status"] = "success" if result.get("success") else "failed"
    root = state["jobs"][key]["domain"] or domain(result.get("final_url", ""))
    if result.get("success") and root and domain(result.get("final_url", "")) == root:
        page = {
            "url": result.get("final_url", ""),
            "html": result.get("content", ""),
            "markdown": "",
            "timestamp": now(),
            "raw_path": raw_rel,
        }
        pages = list(research.get("pages", [])) + [page]
        contacts, _ = _official_contacts(
            stable_id("co", "domain", root), root, pages, c, state["jobs"][key]
        )
        research["official_contacts"] = contacts
        research["pages"] = _compact_pages(pages)


def _balanced_unresolved(state, c):
    row_by_id = {r["research_id"]: r for r in state["rows"]}
    queues = {}
    for spec in c["inputs"]:
        niche = spec["niche"]
        keys = []
        for key, job in state["jobs"].items():
            if niche not in job["niches"]:
                continue
            research = state["research"].get(key, {})
            if research.get("official_contacts") or research.get("search_status") in {"success", "received"}:
                continue
            if research.get("search_status") == "failed" and research.get("search_http_status") not in {429, 502, 503, 504}:
                continue
            has_hint = any(_hint_contacts(row_by_id[rid]) for rid in job["row_ids"])
            keys.append((not has_hint, key))
        queues[niche] = [key for _, key in sorted(keys)]
    totals = {k: len(v) for k, v in queues.items()}
    served = Counter()
    result = []
    used = set()
    while any(queues.values()):
        choices = [n for n, q in queues.items() if q]
        niche = min(choices, key=lambda n: (served[n] / max(1, totals[n]), n))
        key = queues[niche].pop(0)
        if key in used:
            continue
        used.add(key)
        result.append(key)
        served[niche] += 1
        for other in queues.values():
            if key in other:
                other.remove(key)
    return result


def _contact_sort(contacts, roles):
    evidence_rank = {"official_source_backed": 0, "linkedin_candidate": 1, "input_hint_unconfirmed": 2}
    unique = {}
    for item in contacts:
        unique.setdefault((norm(item.get("name")), item.get("evidence_status")), item)
    return sorted(
        unique.values(),
        key=lambda x: (
            evidence_rank.get(x.get("evidence_status"), 9),
            roles.index(x["role"]) if x.get("role") in roles else len(roles),
            x.get("name", ""),
        ),
    )


def _csv_outputs(run, c, state, manifest):
    by_index = defaultdict(list)
    summary = {}
    for row in state["rows"]:
        spec = c["inputs"][row["_input_index"]]
        research = state["research"].get(row["_job_key"], {})
        contacts = list(research.get("official_contacts", [])) + list(research.get("linkedin_contacts", [])) + _hint_contacts(row)
        chosen = _contact_sort(contacts, spec["decision_maker_roles"])[:2]
        result = {k: row.get(k, "") for k in INPUT_FIELDS}
        for prefix, person in zip(("primary", "alternate"), chosen):
            result.update(
                {
                    prefix + "_decision_maker_name": person.get("name", ""),
                    prefix + "_decision_maker_title": person.get("title", ""),
                    prefix + "_role_category": person.get("role", ""),
                    prefix + "_evidence_status": person.get("evidence_status", ""),
                    prefix + "_evidence_url": person.get("evidence_url", ""),
                    prefix + "_linkedin_url": person.get("linkedin_url", ""),
                    prefix + "_selected_email": person.get("email", ""),
                    prefix + "_email_provenance": person.get("email_provenance", ""),
                }
            )
        for prefix in ("primary", "alternate"):
            for suffix in (
                "decision_maker_name", "decision_maker_title", "role_category", "evidence_status", "evidence_url", "linkedin_url", "selected_email", "email_provenance"
            ):
                result.setdefault(prefix + "_" + suffix, "")
        status = chosen[0]["evidence_status"] if chosen else "not_found"
        result["research_outcome"] = status
        gaps = []
        if status == "not_found":
            gaps.append("decision_maker_not_found")
        elif status == "input_hint_unconfirmed":
            gaps.append("decision_maker_unconfirmed")
        if not result["primary_selected_email"]:
            gaps.append("decision_maker_email_not_found")
        result["research_gap"] = " | ".join(gaps)
        result["research_reused"] = "yes" if research.get("reused") else "no"
        result["http_requests"] = research.get("http_requests", 0)
        result["firecrawl_searches"] = research.get("searches", 0)
        result["firecrawl_scrapes"] = research.get("scrapes", 0)
        result["firecrawl_credits_accounted"] = research.get("credits", 0)
        by_index[row["_input_index"]].append(result)
    for index, spec in enumerate(c["inputs"]):
        rows = by_index[index]
        write_csv(run / spec["output_filename"], rows, INPUT_FIELDS + ENRICHED_FIELDS)
        counts = Counter(r["research_outcome"] for r in rows)
        summary[spec["niche"]] = {
            "rows": len(rows),
            "decision_makers_found": sum(r["research_outcome"] in {"official_source_backed", "linkedin_candidate"} for r in rows),
            "official_source_backed": counts["official_source_backed"],
            "linkedin_candidates": counts["linkedin_candidate"],
            "input_hints_unconfirmed": counts["input_hint_unconfirmed"],
            "decision_makers_missing": counts["not_found"],
            "named_emails_found": sum(bool(r["primary_selected_email"]) for r in rows),
            "generic_only_emails": sum(
                not r["primary_selected_email"] and bool(r["generic_or_role_emails"] or r["free_provider_emails"] or r["unclear_or_held_emails"])
                for r in rows
            ),
            "no_email_rows": sum(
                not r["primary_selected_email"] and not (r["generic_or_role_emails"] or r["free_provider_emails"] or r["unclear_or_held_emails"] or r["name_email_candidates"])
                for r in rows
            ),
            "reused_rows": sum(r["research_reused"] == "yes" for r in rows),
            "newly_researched_rows": sum(r["firecrawl_searches"] or r["firecrawl_scrapes"] or r["http_requests"] for r in rows),
        }
    expected = [len(_read_csv(run / "inputs" / Path(spec["path"]).name)) for spec in c["inputs"]]
    actual = [len(by_index[i]) for i in range(len(c["inputs"]))]
    ids = [r["research_id"] for rows in by_index.values() for r in rows]
    maps_receipts = list((run / "receipts" / "maps").glob("*.json"))
    errors = []
    if actual != expected:
        errors.append("output_row_count_mismatch")
    if len(ids) != len(set(ids)):
        errors.append("duplicate_output_research_id")
    if maps_receipts:
        errors.append("maps_requests_present")
    validation = {
        "valid": not errors,
        "errors": errors,
        "expected_rows": expected,
        "output_rows": actual,
        "unique_research_ids": len(set(ids)),
        "unique_job_keys": len(state["jobs"]),
        "maps_requests": len(maps_receipts),
        "by_niche": summary,
        "research_stop_reason": manifest.get("research_stop_reason", ""),
    }
    write_json(run / "validation_report.json", validation)
    write_json(run / "run_summary.json", {**validation, "firecrawl_usage": manifest.get("firecrawl_usage", {})})
    lines = ["# " + c["campaign"], ""]
    for niche, values in summary.items():
        lines.append(
            f"{niche}: {values['rows']} rows; {values['decision_makers_found']} decision makers ({values['official_source_backed']} official, {values['linkedin_candidates']} LinkedIn candidates); {values['named_emails_found']} named emails; {values['decision_makers_missing']} missing decision makers."
        )
    lines.extend(
        [
            "",
            "Maps requests: 0.",
            "Firecrawl usage: " + json.dumps(manifest.get("firecrawl_usage", {}), sort_keys=True) + ".",
            "Research stop: " + (manifest.get("research_stop_reason") or "none") + ".",
            "All email candidates remain mailbox-unverified.",
        ]
    )
    from .common import atomic_text

    atomic_text(run / "run_summary.md", "\n".join(lines) + "\n")
    return validation


def run_input_enrichment(
    c,
    allow_paid=False,
    allow_http=False,
    client=None,
    http_fetcher=None,
    budget_path=ACCOUNT_STATE,
    sleeper=time.sleep,
    search_workers=1,
    search_delay=1.0,
    http_workers=1,
    allow_scrape=True,
):
    if not allow_paid:
        raise ValueError("Exact-input enrichment requires explicit --allow-paid")
    run = initialize_input_run(c)
    reconcile_not_sent(c, budget_path)
    state = read_json(run / "state.json")
    manifest = read_json(run / "manifest.json")
    reg = Registry(c["historical_registry_path"])
    try:
        for key, job in state["jobs"].items():
            research = state["research"].setdefault(
                key,
                {
                    "reused": False,
                    "pages": [],
                    "official_contacts": [],
                    "linkedin_contacts": [],
                    "search_status": "not_started",
                    "http_status": "not_started",
                    "scrape_status": "not_started",
                    "searches": 0,
                    "http_requests": 0,
                    "scrapes": 0,
                    "credits": 0,
                },
            )
            d = job["domain"]
            prior = reg.evidence(d) if d else None
            if prior and not research["pages"]:
                evidence = prior.get("evidence", {})
                research["pages"] = evidence.get("pages", [])
                research["reused"] = bool(research["pages"] or evidence.get("contacts") or evidence.get("emails"))
                contacts, _ = _official_contacts(
                    stable_id("co", "domain", d), d, research["pages"], c, job, evidence
                )
                research["official_contacts"] = contacts
                research["pages"] = _compact_pages(research["pages"])
        write_json(run / "state.json", state)
        fc = client or Firecrawl(True)
        fetch = http_fetcher or http_fetch
        stop = ""
        budget = None
        if not isinstance(search_delay, (int, float)) or not 0 <= search_delay <= 60:
            raise ValueError("search_delay must be 0..60 seconds")
        if type(http_workers) is not int or not 1 <= http_workers <= 16:
            raise ValueError("http_workers must be 1..16")
        # Recover a raw response committed just before an interrupted giant-state
        # checkpoint. The account ledger remains authoritative for whether a call
        # was sent; this only repairs the derived research view.
        for key, research in state["research"].items():
            if research.get("search_status") != "pending":
                continue
            raw = read_json(run / research.get("search_raw_path", ""), {})
            if raw.get("success") and raw.get("payload"):
                payload = raw["payload"]
                items = _search_items(payload)
                research["search_results"] = items
                research["linkedin_contacts"] = _attach_search_named_emails(
                    state["jobs"][key],
                    items,
                    _linkedin_candidates(state["jobs"][key], items),
                )
                research["official_url"] = _official_url(state["jobs"][key], items)
                research["search_results"] = _compact_search_items(items)
                research["search_status"] = "success"
                reported = payload.get("creditsUsed") or (payload.get("data") or {}).get("creditsUsed") or 2
                if not research.get("credits"):
                    research["credits"] = max(2, int(reported))
            elif raw.get("http_status") is not None:
                research["search_status"] = "failed"
                research["search_http_status"] = raw["http_status"]
        # Reconcile a free HTTP fetch interrupted after its raw file commit.
        for key, research in state["research"].items():
            if research.get("http_status") != "pending":
                continue
            raw_rel = research.get("http_raw_path", "")
            result = read_json(run / raw_rel, {})
            if result:
                _apply_http_result(run, c, state, key, research, result, raw_rel)
            else:
                research.update(http_requests=0, http_status="not_started")
        # Compact previously extracted evidence. Complete bodies remain in their
        # immutable raw receipts, reducing this run's checkpoint by tens of MB.
        for research in state["research"].values():
            if research.get("search_results"):
                research["search_results"] = _compact_search_items(research["search_results"])
            if research.get("pages"):
                research["pages"] = _compact_pages(research["pages"])
        write_json(run / "state.json", state)
        execution_usage = {}
        with AccountBudget(fc, manifest["run_id"], c, path=budget_path, sleeper=sleeper) as budget:
            execution_usage = {
                "started_at": now(),
                "starting_balance": budget.run.get("ending_balance"),
                "starting_accounted_credits": budget.used(),
                "starting_searches": sum(
                    int(value.get("searches", 0))
                    for value in state["research"].values()
                ),
                "starting_http_requests": sum(
                    int(value.get("http_requests", 0))
                    for value in state["research"].values()
                ),
                "starting_scrapes": sum(
                    int(value.get("scrapes", 0))
                    for value in state["research"].values()
                ),
            }
            if type(search_workers) is not int or not 1 <= search_workers <= 8:
                raise ValueError("search_workers must be 1..8")
            unresolved = _balanced_unresolved(state, c)
            for start in range(0, len(unresolved), search_workers):
                batch = []
                for key in unresolved[start : start + search_workers]:
                    research = state["research"][key]
                    retryable = (
                        research.get("search_status") == "failed"
                        and research.get("search_http_status") in {429, 502, 503, 504}
                    )
                    if research["searches"] >= c["max_firecrawl_searches_per_domain"] and not retryable:
                        continue
                    job = state["jobs"][key]
                    retry_count = int(research.get("transport_retries", 0))
                    attempt = retry_count + 2 if retryable else 1
                    raw_rel = "raw/firecrawl_search/" + stable_id("job", key) + f"-{attempt}.json"
                    query = _query(job)
                    if retryable:
                        research["transport_retries"] = retry_count + 1
                    else:
                        research["searches"] = 1
                    research.update(
                        search_status="pending",
                        search_query=query,
                        search_raw_path=raw_rel,
                        search_http_status=None,
                    )
                    batch.append(
                        {"key": key, "query": query, "raw_path": str(run / raw_rel)}
                    )
                if not batch:
                    continue
                # Persist intent once per bounded batch; provider/account receipts
                # remain individually durable before and after every network send.
                write_json(run / "state.json", state)
                outcomes, batch_stop = budget.search_batch(
                    batch,
                    c["search_result_limit"],
                    max_workers=search_workers,
                )
                completed = set()
                for outcome in outcomes:
                    request = outcome["request"]
                    key = request["key"]
                    completed.add(key)
                    research = state["research"][key]
                    payload = outcome.get("payload")
                    if payload is None:
                        status = outcome.get("error_status")
                        research["search_status"] = "failed" if status is not None else "pending"
                        research["search_http_status"] = status
                        continue
                    items = _search_items(payload)
                    research["search_results"] = items
                    research["linkedin_contacts"] = _attach_search_named_emails(
                        state["jobs"][key],
                        items,
                        _linkedin_candidates(state["jobs"][key], items),
                    )
                    research["official_url"] = _official_url(state["jobs"][key], items)
                    research["search_results"] = _compact_search_items(items)
                    research["search_status"] = "success"
                    reported = payload.get("creditsUsed") or (payload.get("data") or {}).get("creditsUsed") or 2
                    research["credits"] += max(2, int(reported))
                # Requests not reserved because a hard cap was reached were not sent.
                for request in batch:
                    if request["key"] not in completed:
                        state["research"][request["key"]].update(
                            searches=0, search_status="not_started"
                        )
                write_json(run / "state.json", state)
                if batch_stop:
                    stop = batch_stop
                    break
                if search_delay:
                    sleeper(search_delay)
            if allow_http and stop not in {"uncertain_provider_outcome", "Uncertain Firecrawl operation; reconcile preserved receipt before further spending"}:
                eligible = []
                for key in sorted(state["jobs"]):
                    research = state["research"][key]
                    url = research.get("official_url", "")
                    root = state["jobs"][key]["domain"] or domain(url)
                    if (
                        research.get("official_contacts")
                        or research.get("search_status") not in {"success", "received"}
                        or not url
                        or not root
                        or research["http_requests"] >= c["max_http_pages_per_domain"]
                    ):
                        continue
                    eligible.append((key, url, root))
                for start in range(0, len(eligible), http_workers):
                    batch = eligible[start : start + http_workers]
                    for key, _, _ in batch:
                        raw_rel = "raw/http/" + stable_id("job", key) + "-1.json"
                        state["research"][key].update(
                            http_requests=1, http_status="pending", http_raw_path=raw_rel
                        )
                    write_json(run / "state.json", state)

                    def fetch_one(item):
                        key, url, root = item
                        result = fetch(url, root)
                        raw_rel = state["research"][key]["http_raw_path"]
                        write_json(run / raw_rel, result)
                        return key, result, raw_rel

                    with ThreadPoolExecutor(max_workers=min(http_workers, len(batch))) as pool:
                        fetched = list(pool.map(fetch_one, batch))
                    for key, result, raw_rel in fetched:
                        _apply_http_result(run, c, state, key, state["research"][key], result, raw_rel)
                    write_json(run / "state.json", state)
            if allow_scrape and not stop:
                for key in sorted(state["jobs"]):
                    research = state["research"][key]
                    url = research.get("official_url", "")
                    if (
                        research.get("official_contacts")
                        or research.get("search_status") not in {"success", "received"}
                        or not url
                        or research["scrapes"] >= c["max_firecrawl_scrapes_per_domain"]
                    ):
                        continue
                    raw_rel = "raw/firecrawl/" + stable_id("job", key) + "-1.json"
                    raw = run / raw_rel
                    research.update(scrapes=1, scrape_status="pending", scrape_raw_path=raw_rel)
                    write_json(run / "state.json", state)
                    try:
                        payload = budget.scrape(url, raw)
                    except BudgetStop as exc:
                        saved = read_json(raw, {})
                        if saved.get("success") and saved.get("payload"):
                            payload = saved["payload"]
                            research["scrape_status"] = "received"
                        else:
                            if not raw.exists():
                                research.update(scrapes=0, scrape_status="not_started")
                            stop = str(exc)
                            write_json(run / "state.json", state)
                            break
                    except ProviderError as exc:
                        research["scrape_status"] = "failed" if exc.status is not None else "pending"
                        research["scrape_http_status"] = exc.status
                        write_json(run / "state.json", state)
                        if exc.status is None:
                            stop = "uncertain_provider_outcome"
                            break
                        continue
                    root = state["jobs"][key]["domain"] or domain(url)
                    page = page_from_payload(payload, url, now(), raw_rel)
                    if page and domain(page["url"]) == root:
                        research["pages"].append(page)
                        contacts, _ = _official_contacts(stable_id("co", "domain", root), root, research["pages"], c, state["jobs"][key])
                        research["official_contacts"] = contacts
                        research["pages"] = _compact_pages(research["pages"])
                    research["scrape_status"] = "success"
                    reported = payload.get("creditsUsed") or (payload.get("data") or {}).get("creditsUsed") or 1
                    research["credits"] += max(1, int(reported))
                    write_json(run / "state.json", state)
                    sleeper(1)
            execution_usage.update(
                ending_balance=budget.run.get("ending_balance"),
                ending_accounted_credits=budget.used(),
                ending_searches=sum(
                    int(value.get("searches", 0))
                    for value in state["research"].values()
                ),
                ending_http_requests=sum(
                    int(value.get("http_requests", 0))
                    for value in state["research"].values()
                ),
                ending_scrapes=sum(
                    int(value.get("scrapes", 0))
                    for value in state["research"].values()
                ),
            )
            execution_usage["accounted_credit_delta"] = max(
                0,
                int(execution_usage["ending_accounted_credits"] or 0)
                - int(execution_usage["starting_accounted_credits"] or 0),
            )
            if (
                execution_usage.get("starting_balance") is not None
                and execution_usage.get("ending_balance") is not None
            ):
                execution_usage["balance_delta"] = max(
                    0,
                    int(execution_usage["starting_balance"])
                    - int(execution_usage["ending_balance"]),
                )
        remaining_failures = sum(
            value.get("search_status") == "failed"
            for value in state["research"].values()
        )
        remaining_unstarted = sum(
            value.get("search_status") == "not_started"
            and not value.get("official_contacts")
            for value in state["research"].values()
        )
        if not stop and (remaining_failures or remaining_unstarted):
            stop = "research_gaps_remaining"
        execution_history = list(manifest.get("execution_history", []))
        execution_history.append(execution_usage)
        manifest.update(
            research_status="complete" if not stop else "backlog",
            research_stop_reason=stop,
            maps_requests=0,
            updated_at=now(),
            last_execution_usage=execution_usage,
            execution_history=execution_history,
        )
        if budget and hasattr(budget, "run"):
            manifest["firecrawl_usage"] = {k: v for k, v in budget.run.items() if k != "operations"}
            write_json(run / "receipts" / "firecrawl_usage.json", budget.run)
        write_json(run / "manifest.json", manifest)
        validation = _csv_outputs(run, c, state, manifest)
        return {"run_dir": str(run), **validation}
    finally:
        reg.close()


def input_status(run):
    run = Path(run).resolve()
    return {
        "manifest": read_json(run / "manifest.json"),
        "validation": read_json(run / "validation_report.json"),
    }
