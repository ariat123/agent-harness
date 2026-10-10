"""Canonical staged orchestration; discovery and enrichment share one data model."""

import shutil
import sqlite3
import time
import json
import hashlib
import copy
from contextlib import closing
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .common import domain, identity_keys, norm, now, read_json, stable_id, write_json
from .config import queries
from .extract import extract
from .geography import STATE_NAMES, STATE_REGION, plan
from .providers import ACCOUNT_STATE, AccountBudget, BudgetStop, ProviderError
from .registry import Registry, historical_row


def initialize(config):
    config = dict(config)
    for field in (
        "output_directory",
        "historical_registry_path",
        "suppression_path",
        "continuation_run",
    ):
        if config.get(field):
            config[field] = str(Path(config[field]).resolve())
    if (
        config.get("suppression_path")
        and not Path(config["suppression_path"]).is_file()
    ):
        raise ValueError("Suppression file does not exist")
    run = Path(config["output_directory"]).resolve()
    run.mkdir(parents=True, exist_ok=True)
    saved = read_json(run / "campaign.json")
    if saved is not None:
        if saved != config:
            raise ValueError(
                "Run configuration is immutable; use a new output directory"
            )
        return run
    if any(run.iterdir()):
        raise ValueError("New run directory must be empty")
    reg = Registry(config["historical_registry_path"])
    try:
        reg.snapshot(run / "registry-before.sqlite")
    finally:
        reg.close()
    write_json(run / "campaign.json", config)
    write_json(run / "geography_plan.json", plan(config))
    write_json(
        run / "manifest.json",
        {
            "schema_version": 2,
            "run_id": stable_id("run", str(run), config),
            "campaign": config["campaign"],
            "started_at": now(),
            "discovery_status": "not_started",
            "research_status": "not_started",
            "provider_request_attempts": 0,
        },
    )
    write_json(run / "state.json", {"observations": [], "research": {}, "queue": []})
    # Persist suppression input, not a reference to a mutable external file.
    path = config.get("suppression_path")
    if path:
        import csv

        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            rows = [historical_row(r) for r in csv.DictReader(f)]
        write_json(run / "suppression.json", rows)
    else:
        write_json(run / "suppression.json", [])
    return run


def normalize(item, request, raw_path, index):
    from lead_scraper_core.maps import normalize_company_item

    row = normalize_company_item(item)
    row["root_domain"] = domain(item.get("website") or item.get("site"))
    # Do not conflate a true zero with missing data.
    reviews = item.get("review_count")
    row["review_count"] = str(item.get("reviews", "") if reviews is None else reviews)
    row["rating"] = item.get("rating", "")
    row["state"] = STATE_NAMES.get(row["state"], row["state"])
    if request["country"] == "US" and len(row["zip_code"]) > 5:
        row["zip_code"] = row["zip_code"].split("-")[0]
    if item.get("permanently_closed") is True:
        row["business_status"] = "CLOSED_PERMANENTLY"
    row["latitude"] = item.get("latitude", (item.get("location") or {}).get("lat", ""))
    row["longitude"] = item.get(
        "longitude", (item.get("location") or {}).get("lng", "")
    )
    row.update(
        country=request["country"],
        query_used=request["query"],
        search_location=request["target"]["value"],
        timestamp=request["requested_at"],
        raw_response_path=raw_path,
        source_index=index,
    )
    return row


def objective_reason(row, c):
    if not row.get("company_name"):
        return "missing_company_name"
    reported_country = str(row.get("provider_country") or "").strip().upper()
    if (
        c["country"] == "US"
        and reported_country
        and reported_country
        not in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}
    ):
        return "outside_requested_country"
    if (
        "permanently" in str(row.get("business_status", "")).lower()
        or str(row.get("business_status", "")).upper() == "CLOSED_PERMANENTLY"
    ):
        return "permanently_closed"
    try:
        reviews = int(str(row["review_count"]).replace(",", ""))
    except (ValueError, TypeError):
        return "review_count_unknown"
    if reviews < c["min_reviews"]:
        return "below_min_reviews"
    if c["max_reviews"] is not None and reviews > c["max_reviews"]:
        return "above_max_reviews"
    g = c["geography"]
    state = row.get("state", "").upper()
    if c["country"] == "US":
        if state not in STATE_REGION:
            return "geography_unconfirmed"
        if state in g["exclude_states"] or (
            g["include_states"] and state not in g["include_states"]
        ):
            return "outside_requested_states"
        if g["mode"] == "states" and state not in g["states"]:
            return "outside_requested_states"
        if g["mode"] == "regions" and STATE_REGION[state] not in g["regions"]:
            return "outside_requested_regions"
        if g["mode"] == "zip_codes" and row.get("zip_code") not in g["targets"]:
            return "outside_requested_zip"
        if g["mode"] == "cities" and (
            row.get("city", "").casefold() + ", " + state
        ) not in {v.casefold().rsplit(",", 1)[0] + ", " + v[-2:] for v in g["targets"]}:
            return "outside_requested_city"
    else:
        # Explicit country/city support requires provider-supplied country corroboration.
        if row.get("provider_country", "").upper() != c["country"]:
            return "geography_unconfirmed"
        if row.get("city", "").casefold() not in {
            v.split(",")[0].casefold() for v in g["targets"]
        }:
            return "outside_requested_city"
    return ""


def payload_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("malformed_provider_payload")
    data = payload.get("data", payload.get("results"))
    if isinstance(data, dict):
        data = data.get("results", data.get("places"))
    if not isinstance(data, list):
        raise ValueError("malformed_provider_payload")
    return data


def replay_discovery(run, reg):
    c = read_json(run / "campaign.json")
    manifest = read_json(run / "manifest.json")
    rid = manifest["run_id"]
    with closing(
        sqlite3.connect(
            (run / "registry-before.sqlite").as_uri() + "?mode=ro", uri=True
        )
    ) as baseline:
        initial_keys = {
            (kind, value): cid
            for kind, value, cid in baseline.execute(
                "SELECT kind,value,company_id FROM identities"
            )
        }
    old = read_json(run / "state.json", {})
    obs = []
    snapshots = {}
    included = set()
    seen_locations = set()
    suppressed = {
        k for r in read_json(run / "suppression.json", []) for k in identity_keys(r)
    }
    for path in sorted((run / "receipts/maps").glob("*.json")):
        receipt = read_json(path)
        if receipt["status"] != "success":
            continue
        raw = read_json(run / receipt["raw_path"])
        request = receipt["request"]
        interpretation = receipt.get("interpretation")
        fingerprint = hashlib.sha256(
            (run / receipt["raw_path"]).read_bytes()
        ).hexdigest()
        if interpretation and interpretation["raw_sha256"] != fingerprint:
            raise ValueError("Preserved Maps evidence changed: " + receipt["raw_path"])
        page_observations = []
        for index, item in enumerate(payload_items(raw["payload"])):
            oid = stable_id("obs", receipt["request_id"], index)
            try:
                if not isinstance(item, dict):
                    raise ValueError("malformed_location")
                row = normalize(item, request, receipt["raw_path"], index)
                row["provider_country"] = str(
                    item.get("country_code") or item.get("country") or ""
                )
            except (ValueError, TypeError, AttributeError):
                obs.append(
                    {
                        "observation_id": oid,
                        "disposition": "objective_exclusion",
                        "reason": "malformed_location",
                        "raw_response_path": receipt["raw_path"],
                        "source_index": index,
                    }
                )
                page_observations.append(obs[-1])
                continue
            if interpretation:
                decision = interpretation["decisions"][oid]
                row.update(decision)
                cid = row.get("company_id")
                if cid:
                    snapshot = interpretation["company_snapshots"][cid]
                    reg.restore_observation(row, snapshot)
                    snapshots[cid] = snapshot
                if row["disposition"] == "included":
                    included.add(cid)
                if cid and row["disposition"] != "objective_exclusion":
                    seen_locations.add(
                        row["place_id"] or stable_id("fallback", cid, row["address"])
                    )
                obs.append(row)
                page_observations.append(row)
                continue
            keys = identity_keys(row)
            prior, method = reg.match(row)
            initial_matches = [
                (initial_keys[k], k[0]) for k in keys if k in initial_keys
            ]
            initial_method = (
                method
                if method == "source_backed_parent"
                else initial_matches[0][1]
                if initial_matches
                else method
            )
            reason = objective_reason(row, c)
            cid = None
            if method == "identity_conflict":
                reason = method
            elif not keys:
                reason = "insufficient_identity"
            elif suppressed & set(keys):
                reason = "suppression_match"
            if reason:
                disposition = "objective_exclusion"
                if method != "identity_conflict" and keys:
                    cid, _ = reg.ingest(row, rid)
            else:
                cid, method = reg.ingest(row, rid)
                if not cid:
                    disposition = "objective_exclusion"
                    reason = method
                else:
                    location_key = row["place_id"] or stable_id(
                        "fallback", cid, row["address"]
                    )
                    historical = reg.company(cid)["origin_run"] != rid
                    if location_key in seen_locations:
                        disposition = "duplicate"
                        reason = "duplicate_location"
                    elif historical:
                        disposition = "historical_match"
                        reason = initial_method
                    elif cid in included:
                        disposition = "duplicate"
                        reason = "additional_company_location"
                    elif len(included) >= c["max_net_new_companies"]:
                        disposition = "objective_exclusion"
                        reason = "net_new_target_capacity"
                    else:
                        disposition = "included"
                        reason = "net_new"
                        included.add(cid)
                    seen_locations.add(location_key)
            row.update(
                observation_id=oid,
                company_id=cid or "",
                disposition=disposition,
                reason=reason,
                matched_by=initial_method
                if cid and reg.company(cid)["origin_run"] != rid
                else "same_run",
            )
            if cid:
                row["location_id"] = (
                    stable_id("loc", row["place_id"])
                    if row["place_id"]
                    else stable_id("loc", cid, norm(row["address"]) or rid)
                )
            obs.append(row)
            page_observations.append(row)
        reg.db.commit()
        if not interpretation:
            page_snapshots = {
                row["company_id"]: reg.company(row["company_id"])
                for row in page_observations
                if row.get("company_id")
            }
            snapshots.update(page_snapshots)
            fields = {
                "observation_id",
                "company_id",
                "location_id",
                "disposition",
                "reason",
                "matched_by",
            }
            receipt["interpretation"] = {
                "version": 1,
                "raw_sha256": fingerprint,
                "decisions": {
                    row["observation_id"]: {
                        key: value for key, value in row.items() if key in fields
                    }
                    for row in page_observations
                },
                "company_snapshots": page_snapshots,
            }
            write_json(path, receipt)
    reg.db.commit()
    old["observations"] = obs
    old["company_snapshots"] = snapshots
    old.setdefault("research", {})
    old.setdefault("queue", [])
    write_json(run / "state.json", old)
    manifest.update(net_new_companies=len(included), observed_records=len(obs))
    write_json(run / "manifest.json", manifest)
    return old


def discover(run, reg, provider, sleeper=time.sleep):
    c = read_json(run / "campaign.json")
    manifest = read_json(run / "manifest.json")
    receipt_dir = run / "receipts/maps"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    # Recover an interrupted local commit from preserved raw response, without resending.
    for path in sorted(receipt_dir.glob("*.json")):
        r = read_json(path)
        raw = read_json(run / r["raw_path"], {})
        if r["status"] == "pending" and "payload" in raw:
            try:
                payload_items(raw["payload"])
            except ValueError:
                continue
            r["status"] = "success"
            write_json(path, r)
    state = replay_discovery(run, reg)

    def count():
        return len(list(receipt_dir.glob("*.json")))

    def netnew():
        return len(
            {
                r["company_id"]
                for r in state["observations"]
                if r["disposition"] == "included"
            }
        )

    stopped = "plan_exhausted"
    # Page breadth precedes deeper pagination, with target then query interleaving.
    targets = read_json(run / "geography_plan.json")["targets"]
    uncertain = any(
        read_json(p)["status"] == "pending" for p in receipt_dir.glob("*.json")
    )
    if uncertain:
        stopped = "uncertain_request"
        targets = []
    exhausted = set()
    blocked = set()
    for page in range(1, c["max_pages_per_search"] + 1):
        for target in targets:
            for query in queries(c):
                if netnew() >= c["max_net_new_companies"]:
                    stopped = "net_new_target"
                    break
                key = stable_id("search", query, target["value"])
                if key in exhausted or key in blocked:
                    continue
                reqid = stable_id(
                    "request", query, target["value"], page, c["country"], c["language"]
                )
                prior = [
                    read_json(p)
                    for p in sorted(receipt_dir.glob("*.json"))
                    if read_json(p)["request_id"] == reqid
                ]
                successful = next((r for r in prior if r["status"] == "success"), None)
                if successful:
                    if not payload_items(
                        read_json(run / successful["raw_path"])["payload"]
                    ):
                        exhausted.add(key)
                    continue
                if any(r["status"] == "pending" for r in prior):
                    blocked.add(key)
                    stopped = "uncertain_request"
                    continue
                if prior and (
                    c["resume_policy"] == "skip_failed"
                    or prior[-1].get("http_status") not in (429, 500, 502, 503, 504)
                ):
                    blocked.add(key)
                    continue
                for attempt in range(len(prior), c["max_retries"] + 1):
                    if count() >= c["max_maps_requests"]:
                        stopped = "maps_request_ceiling"
                        break
                    seq = count() + 1
                    path = receipt_dir / f"{seq:06d}.json"
                    raw_path = f"raw/maps/{seq:06d}.json"
                    request = {
                        "query": query,
                        "target": target,
                        "page": page,
                        "country": c["country"],
                        "language": c["language"],
                        "requested_at": now(),
                    }
                    receipt = {
                        "request_id": reqid,
                        "attempt": attempt + 1,
                        "status": "pending",
                        "request": request,
                        "raw_path": raw_path,
                    }
                    write_json(path, receipt)
                    try:
                        payload = provider.page(
                            query + " " + target["value"],
                            page,
                            c["country"],
                            c["language"],
                        )
                        payload_items(payload)
                    except ProviderError as exc:
                        receipt.update(
                            status="failed" if exc.status is not None else "pending",
                            http_status=exc.status,
                            error=exc.error,
                        )
                        write_json(
                            run / raw_path,
                            {
                                "success": False,
                                "http_status": exc.status,
                                "error": exc.error,
                            },
                        )
                        write_json(path, receipt)
                        if exc.status is None:
                            uncertain = True
                            stopped = "uncertain_request"
                            blocked.add(key)
                            break
                        if (
                            exc.status in (429, 500, 502, 503, 504)
                            and attempt < c["max_retries"]
                        ):
                            sleeper(min(30, 2**attempt))
                            continue
                        blocked.add(key)
                        break
                    except ValueError:
                        receipt.update(
                            status="failed", error="malformed_provider_payload"
                        )
                        write_json(run / raw_path, {"payload": payload})
                        write_json(path, receipt)
                        blocked.add(key)
                        break
                    write_json(run / raw_path, {"payload": payload})
                    receipt["status"] = "success"
                    write_json(path, receipt)
                    state = replay_discovery(run, reg)
                    if not payload_items(payload):
                        exhausted.add(key)
                    sleeper(c["maps_interval_seconds"])
                    break
                if uncertain:
                    break
                if count() >= c["max_maps_requests"]:
                    stopped = "maps_request_ceiling"
                    break
            if (
                uncertain
                or netnew() >= c["max_net_new_companies"]
                or count() >= c["max_maps_requests"]
            ):
                break
        if (
            uncertain
            or netnew() >= c["max_net_new_companies"]
            or count() >= c["max_maps_requests"]
        ):
            break
    manifest = read_json(run / "manifest.json")
    pending = sum(
        read_json(p)["status"] == "pending" for p in receipt_dir.glob("*.json")
    )
    manifest.update(
        discovery_status="needs_attention" if pending or blocked else "complete",
        discovery_stop_reason=stopped,
        maps_requests=count(),
        updated_at=now(),
    )
    write_json(run / "manifest.json", manifest)
    return state


def links(pages, root):
    candidates = {}
    allowed = [
        "leadership",
        "team",
        "management",
        "about",
        "contact",
        "locations",
        "location",
    ]
    for page in pages:
        soup = BeautifulSoup(page.get("html", ""), "html.parser")
        raw = [
            (a.get("href", ""), a.get_text(" ", strip=True))
            for a in soup.select("a[href]")
        ]
        import re

        raw += [
            (m[1], m[0])
            for m in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", page.get("markdown", ""))
        ]
        for href, label in raw:
            url = urljoin(page["url"], href).split("#")[0]
            parsed = urlparse(url)
            if (
                parsed.scheme not in ["https", "http"]
                or parsed.username
                or parsed.password
                or parsed.query
                or domain(url) != root
            ):
                continue
            if parsed.path.lower().endswith((".pdf", ".jpg", ".png", ".zip", ".mp4")):
                continue
            match = next(
                (
                    i
                    for i, t in enumerate(allowed)
                    if t in (parsed.path + " " + label).lower()
                ),
                None,
            )
            if match is not None:
                candidates[url] = match
    return sorted(candidates, key=lambda u: (candidates[u], u))


def page_from_payload(payload, url, stamp, raw_path):
    from lead_scraper_core.web_enrichment import normalize_firecrawl_document

    doc = normalize_firecrawl_document(payload, url, domain(url))
    # Provider redirects off the official domain must not become official evidence.
    data = payload.get("data", payload)
    meta = data.get("metadata") or {}
    final = meta.get("url") or meta.get("sourceURL") or url
    if domain(final) != domain(url):
        return None
    if not doc.html and not doc.markdown:
        return None
    try:
        if int(meta.get("statusCode") or 200) >= 400:
            return None
    except (ValueError, TypeError):
        return None
    body = (
        doc.markdown or BeautifulSoup(doc.html, "html.parser").get_text(" ")
    ).lower()
    if len(body) < 1000 and any(
        term in body
        for term in (
            "access denied",
            "verify you are human",
            "checking your browser",
            "captcha",
        )
    ):
        return None
    return {
        "url": doc.source_url,
        "html": doc.html,
        "markdown": doc.markdown,
        "timestamp": stamp,
        "raw_path": raw_path,
    }


def build_queue(run, reg, backlog=False):
    state = read_json(run / "state.json")
    config = read_json(run / "campaign.json")
    continuation = config.get("continuation_run")
    if continuation:
        source = Path(continuation).resolve()
        if not source.is_dir():
            raise ValueError("Continuation run does not exist")
        if source == Path(run).resolve():
            raise ValueError("Continuation run must differ from output run")
        if state.get("continuation_source_run"):
            if Path(state["continuation_source_run"]).resolve() != source:
                raise ValueError("Continuation source is immutable")
            return state["queue"]
        source_state = read_json(source / "state.json")
        if not isinstance(source_state, dict) or not source_state.get("queue"):
            raise ValueError("Continuation run has no preserved research queue")

        def absolute_receipt_paths(receipt):
            copied = copy.deepcopy(receipt)
            for item in copied.get("pages", []) + copied.get("attempts", []):
                raw = item.get("raw_path")
                if raw and not Path(raw).is_absolute():
                    item["raw_path"] = str((source / raw).resolve())
            return copied

        state["observations"] = copy.deepcopy(source_state.get("observations", []))
        state["company_snapshots"] = copy.deepcopy(
            source_state.get("company_snapshots", {})
        )
        state["queue"] = copy.deepcopy(source_state["queue"])
        state["research"] = {
            domain_name: absolute_receipt_paths(receipt)
            for domain_name, receipt in source_state.get("research", {}).items()
        }
        state["continuation_source_run"] = str(source)
        write_json(run / "state.json", state)
        return state["queue"]
    ids = {
        r["company_id"]
        for r in state["observations"]
        if r.get("company_id")
        and r["disposition"] in ["included", "historical_match", "duplicate"]
    }
    if backlog:
        terms = [
            term.rstrip("s")
            for term in norm(config["business_category"]).split()
            if term
            not in {"company", "companies", "service", "services", "the", "and", "of"}
        ]
        for company in reg.backlog():
            row = json.loads(company["data"])
            source_text = norm(
                " ".join(
                    str(row.get(field) or "")
                    for field in ("company_name", "category", "query_used")
                )
            )
            # Imported backlog can contain unrelated campaigns. Apply the same
            # objective source filters before allowing this campaign to spend.
            if (
                not objective_reason(row, config)
                and terms
                and all(term in source_text for term in terms)
            ):
                ids.add(company["company_id"])
    queue = []
    for cid in sorted(ids):
        co = reg.company(cid)
        state.setdefault("company_snapshots", {}).setdefault(cid, co)
        root = co["root_domain"]
        if root and root not in {r["domain"] for r in queue}:
            queue.append(
                {
                    "company_id": cid,
                    "domain": root,
                    "url": "https://" + root + "/",
                    "company_snapshot": state["company_snapshots"][cid],
                }
            )
    state["queue"] = queue
    write_json(run / "state.json", state)
    return queue


def enrich(
    run,
    reg,
    client=None,
    http_fetch=None,
    backlog=False,
    budget_path=ACCOUNT_STATE,
    sleeper=time.sleep,
):
    c = read_json(run / "campaign.json")
    manifest = read_json(run / "manifest.json")
    queue = build_queue(run, reg, backlog)
    state = read_json(run / "state.json")
    jobs = []
    for job in queue:
        d = job["domain"]
        prior = reg.evidence(d)
        local = state["research"].get(d)
        if (
            not local
            and prior
            and prior["status"] == "complete"
            and not c["refresh_research"]
        ):
            evidence = extract(
                job["company_id"], d, prior["evidence"].get("pages", []), c
            )
            if evidence["pages"]:
                state["research"][d] = {
                    "status": "complete",
                    "reused": True,
                    "evidence": evidence,
                    "http_attempts": 0,
                    "firecrawl_attempts": 0,
                }
                continue
        receipt = local or {
            "status": "pending",
            "reused": False,
            "pages": [],
            "http_attempts": 0,
            "firecrawl_attempts": 0,
            "attempts": [],
        }
        if receipt.get("status") == "complete":
            continue
        if not local and prior and not c["refresh_research"]:
            receipt["pages"] = prior["evidence"].get("pages", [])
            receipt["attempts"] = prior["evidence"].get("operations", [])
            receipt["firecrawl_attempts"] = sum(
                a["provider"] == "firecrawl" for a in receipt["attempts"]
            )
            receipt["http_attempts"] = sum(
                a["provider"] == "http" for a in receipt["attempts"]
            )
        receipt.setdefault("pages", [])
        receipt.setdefault("attempts", [])
        receipt.setdefault("http_attempts", 0)
        receipt.setdefault("firecrawl_attempts", 0)
        state["research"][d] = receipt
        jobs.append((job, receipt))

    def persist():
        for domain_name, receipt in state["research"].items():
            job = next((j for j in queue if j["domain"] == domain_name), None)
            if job:
                write_json(
                    run
                    / "receipts/research"
                    / (stable_id("domain", domain_name) + ".json"),
                    {"job": job, "receipt": receipt},
                )
        write_json(run / "state.json", state)

    persist()
    # Bounded HTTP follows the same page evidence schema, with one homepage pass first.
    if http_fetch is not None:
        for depth in range(c["max_http_pages_per_domain"]):
            for job, r in jobs:
                if r["http_attempts"] > depth:
                    continue
                visited = {a["url"] for a in r["attempts"] if a["provider"] == "http"}
                choices = [job["url"]] + links(r["pages"], job["domain"])
                url = next((u for u in choices if u not in visited), None)
                if not url:
                    continue
                r["http_attempts"] += 1
                raw = f"raw/http/{stable_id('domain', job['domain'])}-{r['http_attempts']}.json"
                a = {
                    "provider": "http",
                    "url": url,
                    "status": "pending",
                    "raw_path": raw,
                    "timestamp": now(),
                }
                r["attempts"].append(a)
                persist()
                result = http_fetch(url, job["domain"])
                write_json(run / raw, result)
                a["status"] = "success" if result["success"] else "failed"
                if (
                    result["success"]
                    and domain(result.get("final_url", url)) == job["domain"]
                ):
                    r["pages"].append(
                        {
                            "url": result.get("final_url", url),
                            "html": result.get("content", ""),
                            "markdown": "",
                            "timestamp": a["timestamp"],
                            "raw_path": raw,
                        }
                    )
                persist()
    # Recover research receipt commits from raw evidence without issuing provider requests.
    for job, r in jobs:
        for a in r["attempts"]:
            rawdata = read_json(run / a["raw_path"], {})
            if (
                a["status"] == "pending"
                and rawdata.get("success")
                and "payload" in rawdata
            ):
                a["status"] = "success"
                p = page_from_payload(
                    rawdata["payload"], a["url"], a["timestamp"], a["raw_path"]
                )
                if p and not any(x["raw_path"] == p["raw_path"] for x in r["pages"]):
                    r["pages"].append(p)
    persist()
    stop = ""
    budget = None
    if (
        jobs
        and client is not None
        and c["max_firecrawl_pages_per_domain"]
        and c["max_firecrawl_credit_delta"]
    ):
        try:
            with AccountBudget(
                client, manifest["run_id"], c, path=budget_path, sleeper=sleeper
            ) as budget:
                # Round-robin page passes prioritize company breadth.
                for depth in range(c["max_firecrawl_pages_per_domain"]):
                    for job, r in jobs:
                        if (
                            r["firecrawl_attempts"]
                            >= c["max_firecrawl_pages_per_domain"]
                        ):
                            continue
                        fc_success = {
                            a["url"]
                            for a in r["attempts"]
                            if a["provider"] == "firecrawl" and a["status"] == "success"
                        }
                        prior_fc = [
                            p["url"]
                            for p in r["pages"]
                            if "/firecrawl/" in p.get("raw_path", "")
                        ]
                        choices = [job["url"]] + links(r["pages"], job["domain"])
                        url = next(
                            (
                                u
                                for u in choices
                                if u not in fc_success and u not in prior_fc
                            ),
                            None,
                        )
                        if not url:
                            continue
                        if any(
                            a["status"] == "pending" and a["provider"] == "firecrawl"
                            for a in r["attempts"]
                        ):
                            raise BudgetStop("uncertain_domain_operation")
                        failures = [
                            a
                            for a in r["attempts"]
                            if a["provider"] == "firecrawl"
                            and a["url"] == url
                            and a["status"] == "failed"
                        ]
                        if failures and (
                            c["resume_policy"] == "skip_failed"
                            or len(failures) > c["max_retries"]
                            or failures[-1].get("http_status") != 429
                        ):
                            continue
                        seq = r["firecrawl_attempts"] + 1
                        raw = f"raw/firecrawl/{stable_id('domain', job['domain'])}-{seq}.json"
                        a = {
                            "provider": "firecrawl",
                            "url": url,
                            "status": "pending",
                            "raw_path": raw,
                            "timestamp": now(),
                        }
                        r["attempts"].append(a)
                        r["firecrawl_attempts"] = seq
                        persist()
                        try:
                            payload = budget.scrape(url, run / raw)
                        except BudgetStop:
                            if not (run / raw).exists():
                                r["attempts"].remove(a)
                                r["firecrawl_attempts"] -= 1
                            else:
                                rawdata = read_json(run / raw)
                                if rawdata.get("success"):
                                    a["status"] = "success"
                                    p = page_from_payload(
                                        rawdata["payload"], url, a["timestamp"], raw
                                    )
                                    if p:
                                        r["pages"].append(p)
                            persist()
                            raise
                        except ProviderError as exc:
                            a.update(
                                status="failed"
                                if exc.status is not None
                                else "pending",
                                http_status=exc.status,
                            )
                            persist()
                            if exc.status == 429:
                                sleeper(min(30, 2 ** len(failures)))
                                continue
                            if exc.status == 402 or exc.status is None:
                                raise BudgetStop(
                                    "credit_error"
                                    if exc.status == 402
                                    else "uncertain_provider_outcome"
                                ) from None
                            continue
                        a["status"] = "success"
                        p = page_from_payload(payload, url, a["timestamp"], raw)
                        if p:
                            r["pages"].append(p)
                        persist()
                        sleeper(1)
        except (BudgetStop, ProviderError) as exc:
            stop = str(exc)
    for job, r in jobs:
        evidence = extract(job["company_id"], job["domain"], r["pages"], c)
        successes = sum(
            a["provider"] == "firecrawl" and a["status"] == "success"
            for a in r["attempts"]
        )
        possible = links(r["pages"], job["domain"])
        fc_done = successes >= c["max_firecrawl_pages_per_domain"] or (
            successes > 0 and not possible
        )
        if c["max_firecrawl_pages_per_domain"] == 0:
            fc_done = bool(r["pages"])
        r["status"] = "complete" if r["pages"] and fc_done else "backlog"
        r["reason"] = (
            stop or ("firecrawl_pending" if not fc_done else "no_usable_content")
            if r["status"] != "complete"
            else ""
        )
        r["evidence"] = evidence
        shared = copy_evidence_for_registry(evidence, run, r)
        reg.save_research(
            job["company_id"], job["domain"], r["status"], shared, r["reason"]
        )
    reg.db.commit()
    persist()
    manifest.update(
        research_status="backlog"
        if any(r["status"] != "complete" for r in state["research"].values())
        else "complete",
        research_stop_reason=stop,
        updated_at=now(),
    )
    if budget and hasattr(budget, "run"):
        manifest["firecrawl_usage"] = {
            k: v for k, v in budget.run.items() if k != "operations"
        }
        write_json(run / "receipts/firecrawl_usage.json", budget.run)
    write_json(run / "manifest.json", manifest)
    return state


def http_fetch(url, root):
    from lead_scraper_core.web_enrichment import fetch_http_page

    return fetch_http_page(url, timeout_seconds=8, expected_root_domain=root).to_dict()


def rebuild(source, destination):
    """Isolated offline reconstruction. Baseline snapshot fixes historical decisions."""
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if destination == source or (destination.exists() and any(destination.iterdir())):
        raise ValueError("Rebuild destination must be new/empty and distinct")
    destination.mkdir(parents=True, exist_ok=True)
    for name in [
        "campaign.json",
        "geography_plan.json",
        "manifest.json",
        "suppression.json",
        "registry-before.sqlite",
    ]:
        shutil.copy2(source / name, destination / name)
    for name in ["raw", "receipts"]:
        if (source / name).exists():
            shutil.copytree(source / name, destination / name, dirs_exist_ok=True)
    shutil.copy2(
        source / "registry-before.sqlite", destination / "rebuild-registry.sqlite"
    )
    reg = Registry(destination / "rebuild-registry.sqlite")
    try:
        state = replay_discovery(destination, reg)
        original = read_json(source / "state.json", {})
        if not original.get("research"):
            domain_receipts = [
                read_json(p)
                for p in sorted((source / "receipts/research").glob("*.json"))
            ]
            original = {
                "queue": [r["job"] for r in domain_receipts],
                "research": {r["job"]["domain"]: r["receipt"] for r in domain_receipts},
            }
        c = read_json(source / "campaign.json")
        # Research requests/evidence pointers are an immutable interpretation ledger;
        # output tables and extraction results are always regenerated from raw pages.
        state["queue"] = original.get("queue", [])
        for job in state["queue"]:
            if job.get("company_snapshot"):
                state.setdefault("company_snapshots", {}).setdefault(
                    job["company_id"], job["company_snapshot"]
                )
        for d, r in original.get("research", {}).items():
            pages = []
            for p in r.get("evidence", {}).get("pages", r.get("pages", [])):
                rawpath = Path(p.get("raw_path", ""))
                actual = destination / rawpath if not rawpath.is_absolute() else rawpath
                if actual.is_file():
                    data = read_json(actual)
                    if data.get("success") and "payload" in data:
                        rebuilt = page_from_payload(
                            data["payload"], p["url"], p["timestamp"], str(rawpath)
                        )
                        if rebuilt:
                            pages.append(rebuilt)
                    elif data.get("success") and "content" in data:
                        pages.append({**p, "html": data["content"], "markdown": ""})
                    elif "response" in data:
                        payload = data["response"].get("provider_response", {})
                        rebuilt = page_from_payload(
                            payload, p["url"], p["timestamp"], str(rawpath)
                        )
                        if rebuilt:
                            pages.append(rebuilt)
                elif r.get("reused"):
                    # Shared evidence snapshot is preserved locally when reused.
                    pages.append(p)
                else:
                    raise ValueError("Missing raw research evidence: " + str(rawpath))
            cid = next(
                (j["company_id"] for j in state["queue"] if j["domain"] == d), None
            )
            if cid:
                state["research"][d] = {**r, "evidence": extract(cid, d, pages, c)}
        write_json(destination / "state.json", state)
        from .outputs import export

        result = export(destination, reg)
        return result
    finally:
        reg.close()


def copy_evidence_for_registry(evidence, run, receipt):
    import copy

    result = copy.deepcopy(evidence)
    result["operations"] = copy.deepcopy(receipt.get("attempts", []))
    for item in result["pages"] + result["operations"]:
        if item.get("raw_path"):
            item["raw_path"] = str((run / item["raw_path"]).resolve())
    return result
