"""Versioned persistent companies, evidence, appearances and import provenance."""

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from .common import (
    domain,
    identity_keys,
    norm,
    now,
    read_json,
    redact,
    stable_id,
    write_json,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS companies(company_id TEXT PRIMARY KEY, company_name TEXT, root_domain TEXT, origin_run TEXT, first_seen TEXT, last_seen TEXT, research_status TEXT DEFAULT 'pending', data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identities(kind TEXT, value TEXT, company_id TEXT REFERENCES companies, PRIMARY KEY(kind,value));
CREATE TABLE IF NOT EXISTS locations(location_id TEXT PRIMARY KEY, company_id TEXT REFERENCES companies, place_id TEXT, first_seen TEXT, last_seen TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS domains(domain TEXT PRIMARY KEY, company_id TEXT REFERENCES companies, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS phones(phone TEXT, company_id TEXT REFERENCES companies, first_seen TEXT, last_seen TEXT, PRIMARY KEY(phone,company_id));
CREATE TABLE IF NOT EXISTS appearances(run_id TEXT, company_id TEXT REFERENCES companies, disposition TEXT, first_seen TEXT, PRIMARY KEY(run_id,company_id));
CREATE TABLE IF NOT EXISTS research(domain TEXT PRIMARY KEY, company_id TEXT REFERENCES companies, status TEXT, evidence_date TEXT, evidence TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS contacts(contact_id TEXT PRIMARY KEY, company_id TEXT REFERENCES companies, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS emails(email_id TEXT PRIMARY KEY, company_id TEXT REFERENCES companies, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_artifacts(artifact_id TEXT PRIMARY KEY, path TEXT, sha256 TEXT, confidence TEXT, imported_at TEXT, report TEXT);
CREATE TABLE IF NOT EXISTS source_rows(artifact_id TEXT, row_number INTEGER, company_id TEXT, confidence TEXT, reason TEXT, data TEXT, PRIMARY KEY(artifact_id,row_number));
CREATE TABLE IF NOT EXISTS relationships(child_id TEXT, parent_id TEXT, source_url TEXT NOT NULL, evidence_date TEXT NOT NULL, PRIMARY KEY(child_id,parent_id,source_url));
"""


class Registry:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        versions = [
            r[0] for r in self.db.execute("SELECT version FROM schema_versions")
        ]
        if any(v > 1 for v in versions):
            raise ValueError("Registry schema is newer than this CLI")
        self.db.execute("INSERT OR IGNORE INTO schema_versions VALUES(1,?)", (now(),))
        self.db.commit()

    def close(self):
        self.db.close()

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def snapshot(self, path):
        dest = sqlite3.connect(path)
        try:
            self.db.backup(dest)
        finally:
            dest.close()

    def match(self, row):
        matches = []
        for kind, value in identity_keys(row):
            r = self.db.execute(
                "SELECT company_id FROM identities WHERE kind=? AND value=?",
                (kind, value),
            ).fetchone()
            if r:
                company_id = self.canonical_company(r[0])
                if company_id is None:
                    return None, "identity_conflict"
                matches.append(
                    (
                        company_id,
                        "source_backed_parent" if company_id != r[0] else kind,
                        value,
                    )
                )
        if len({r[0] for r in matches}) > 1:
            return None, "identity_conflict"
        return (matches[0][0], matches[0][1]) if matches else (None, "")

    def canonical_company(self, company_id):
        seen = set()
        while company_id not in seen:
            seen.add(company_id)
            parents = {
                r[0]
                for r in self.db.execute(
                    "SELECT parent_id FROM relationships WHERE child_id=?",
                    (company_id,),
                )
            }
            if not parents:
                return company_id
            if len(parents) != 1:
                return None
            company_id = parents.pop()
        return None

    def ingest(self, row, run_id):
        keys = identity_keys(row)
        cid, method = self.match(row)
        if method == "identity_conflict" or not keys:
            return None, method or "insufficient_identity"
        stamp = row.get("timestamp") or now()
        if not cid:
            cid = stable_id("co", *keys[0])
            self.db.execute(
                "INSERT INTO companies VALUES(?,?,?,?,?,?,?,?)",
                (
                    cid,
                    row.get("company_name", ""),
                    row.get("root_domain", ""),
                    run_id,
                    stamp,
                    stamp,
                    "pending",
                    json.dumps(row),
                ),
            )
        else:
            old = self.company(cid)
            merged = {
                **old["data"],
                **{k: v for k, v in row.items() if v not in ("", None)},
            }
            self.db.execute(
                "UPDATE companies SET last_seen=?, company_name=?, root_domain=?, data=? WHERE company_id=?",
                (
                    max(stamp, old["last_seen"]),
                    old["company_name"] or row.get("company_name", ""),
                    old["root_domain"] or row.get("root_domain", ""),
                    json.dumps(merged),
                    cid,
                ),
            )
        for kind, value in keys:
            self.db.execute(
                "INSERT OR IGNORE INTO identities VALUES(?,?,?)", (kind, value, cid)
            )
            if kind == "domain":
                self.db.execute(
                    "INSERT INTO domains VALUES(?,?,?,?) ON CONFLICT(domain) DO UPDATE SET last_seen=excluded.last_seen",
                    (value, cid, stamp, stamp),
                )
            if kind == "phone":
                self.db.execute(
                    "INSERT INTO phones VALUES(?,?,?,?) ON CONFLICT(phone,company_id) DO UPDATE SET last_seen=excluded.last_seen",
                    (value, cid, stamp, stamp),
                )
        lid = (
            stable_id("loc", row["place_id"])
            if row.get("place_id")
            else stable_id("loc", cid, norm(row.get("address")) or run_id)
        )
        self.db.execute(
            "INSERT INTO locations VALUES(?,?,?,?,?,?) ON CONFLICT(location_id) DO UPDATE SET last_seen=excluded.last_seen,data=excluded.data",
            (lid, cid, row.get("place_id", ""), stamp, stamp, json.dumps(row)),
        )
        self.db.execute(
            "INSERT OR IGNORE INTO appearances VALUES(?,?,?,?)",
            (run_id, cid, "discovered", stamp),
        )
        return cid, method or "new"

    def company(self, cid):
        row = dict(
            self.db.execute(
                "SELECT * FROM companies WHERE company_id=?", (cid,)
            ).fetchone()
        )
        row["data"] = json.loads(row["data"])
        return row

    def restore_observation(self, row, snapshot):
        """Rehydrate missing registry state from a committed interpretation receipt.

        Existing shared registry identities are never overwritten. This is used
        by offline rebuild and remains idempotent during ordinary resume.
        """
        cid = snapshot["company_id"]
        self.db.execute(
            "INSERT OR IGNORE INTO companies VALUES(?,?,?,?,?,?,?,?)",
            (
                cid,
                snapshot["company_name"],
                snapshot["root_domain"],
                snapshot["origin_run"],
                snapshot["first_seen"],
                snapshot["last_seen"],
                snapshot["research_status"],
                json.dumps(snapshot["data"]),
            ),
        )
        for kind, value in identity_keys(row):
            self.db.execute(
                "INSERT OR IGNORE INTO identities VALUES(?,?,?)", (kind, value, cid)
            )
        if row.get("location_id"):
            self.db.execute(
                "INSERT OR IGNORE INTO locations VALUES(?,?,?,?,?,?)",
                (
                    row["location_id"],
                    cid,
                    row.get("place_id", ""),
                    row.get("timestamp", ""),
                    row.get("timestamp", ""),
                    json.dumps(row),
                ),
            )

    def evidence(self, d):
        r = self.db.execute("SELECT * FROM research WHERE domain=?", (d,)).fetchone()
        if not r:
            return None
        r = dict(r)
        r["evidence"] = json.loads(r["evidence"])
        return r

    def save_research(self, cid, d, status, evidence, error=""):
        self.db.execute(
            "INSERT INTO research VALUES(?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET status=excluded.status,evidence_date=excluded.evidence_date,evidence=excluded.evidence,error=excluded.error",
            (d, cid, status, now(), json.dumps(redact(evidence)), error),
        )
        self.db.execute(
            "UPDATE companies SET research_status=? WHERE company_id=?", (status, cid)
        )
        for table, idfield, items in [
            ("contacts", "contact_id", evidence.get("contacts", [])),
            ("emails", "email_id", evidence.get("emails", [])),
        ]:
            self.db.execute(f"DELETE FROM {table} WHERE company_id=?", (cid,))
            for item in items:
                self.db.execute(
                    f"INSERT OR REPLACE INTO {table} VALUES(?,?,?)",
                    (item[idfield], cid, json.dumps(redact(item))),
                )

    def relate(self, child, parent, source_url, evidence_date):
        if not source_url.startswith(("https://", "http://")) or not evidence_date:
            raise ValueError("Parent relationship requires dated source evidence")
        if child == parent or self.canonical_company(parent) == child:
            raise ValueError("Parent relationship must not create a cycle")
        if any(
            not self.db.execute(
                "SELECT 1 FROM companies WHERE company_id=?", (cid,)
            ).fetchone()
            for cid in (child, parent)
        ):
            raise ValueError("Parent relationship requires two existing companies")
        self.db.execute(
            "INSERT OR IGNORE INTO relationships VALUES(?,?,?,?)",
            (child, parent, source_url, evidence_date),
        )
        # Source IDs/rows remain intact; identity lookup follows only explicit
        # dated relationships, never inferred similarity in names or ownership.

    def backlog(self):
        return self.rows(
            "SELECT DISTINCT c.* FROM companies c WHERE c.root_domain != '' AND c.research_status != 'complete' ORDER BY c.first_seen,c.company_id"
        )


ALIASES = {
    "company_name": [
        "company_name",
        "business_name",
        "company",
        "company name",
        "business",
        "name",
        "organization_name",
        "organization",
    ],
    "place_id": ["place_id", "google_place_id", "google_id"],
    "website": ["website", "company_website", "website_url", "site", "url"],
    "root_domain": ["root_domain", "domain", "company_domain"],
    "phone": ["phone", "phone_number", "normalized_phone", "business_phone"],
    "address": ["address", "full_address", "street_address", "business_address"],
    "city": ["city"],
    "state": ["state", "state_code"],
    "zip_code": ["zip_code", "postal_code", "zip"],
    "review_count": ["review_count", "google_review_count", "google_reviews"],
    "rating": ["rating", "google_rating"],
    "category": ["category", "google_category", "categories"],
    "timestamp": [
        "scrape_timestamp",
        "date_scraped",
        "first_seen",
        "first_seen_at",
        "timestamp",
    ],
}


def historical_row(row):
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    result = {
        k: next(
            (
                str(lowered[a]).strip()
                for a in aa
                if lowered.get(a) is not None and str(lowered[a]).strip()
            ),
            "",
        )
        for k, aa in ALIASES.items()
    }
    result["root_domain"] = domain(result["root_domain"] or result["website"])
    result["country"] = "US"
    return result


def import_history(reg, roots, report_path=None):
    csv.field_size_limit(16 * 1024 * 1024)
    paths = set()
    for value in roots:
        p = Path(value)
        if p.is_file():
            paths.add(p.resolve())
        elif p.is_dir():
            for ext in ("*.csv", "*.db", "*.sqlite"):
                paths.update(
                    x.resolve()
                    for x in p.rglob(ext)
                    if not any(
                        v in x.parts
                        for v in (".venv", ".git", "node_modules", "rebuild")
                    )
                    and "registry-before.sqlite" != x.name
                )
    report = {
        "imported_artifacts": 0,
        "unchanged_artifacts": 0,
        "accepted_rows": 0,
        "quarantined_rows": 0,
        "errors": [],
        "artifacts": [],
    }
    for path in sorted(paths):
        if path == reg.path.resolve() or path.name.endswith("-wal"):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        aid = stable_id("artifact", str(path), digest)
        if reg.db.execute(
            "SELECT 1 FROM source_artifacts WHERE artifact_id=?", (aid,)
        ).fetchone():
            report["unchanged_artifacts"] += 1
            continue
        stats = {"path": str(path), "accepted": 0, "quarantined": 0, "sha256": digest}

        def consume(rows, table=""):
            for index, raw in enumerate(rows, 1):
                raw = dict(raw)
                row = historical_row(raw)
                keys = identity_keys(row)
                # Source rows remain evidence even if they lack an authoritative company key.
                cid, why = (
                    reg.ingest(row, "history:" + aid)
                    if keys
                    else (None, "insufficient_identity")
                )
                confidence = "high" if row["place_id"] else "medium" if keys else "low"
                if cid:
                    stats["accepted"] += 1
                    # Legacy email claims keep their original fields; no inferred verification promotion.
                    lowered = {str(k).lower(): v for k, v in raw.items()}
                    email = (
                        str(lowered.get("email") or lowered.get("primary_email") or "")
                        .strip()
                        .lower()
                    )
                    source = str(
                        lowered.get("email_source_url")
                        or lowered.get("source_page")
                        or lowered.get("email_source")
                        or ""
                    )
                    if "@" in email and " " not in email:
                        eid = stable_id("email", cid, email, "historical")
                        item = {
                            "email_id": eid,
                            "company_id": cid,
                            "contact_id": "",
                            "email": email,
                            "status": "unverified",
                            "provenance": "historical_import",
                            "deliverability": "unverified",
                            "source_url": source,
                            "evidence_date": row["timestamp"],
                            "source_artifact": str(path),
                        }
                        reg.db.execute(
                            "INSERT OR IGNORE INTO emails VALUES(?,?,?)",
                            (eid, cid, json.dumps(item)),
                        )
                else:
                    stats["quarantined"] += 1
                rowid = (
                    index
                    if not table
                    else int(
                        hashlib.sha256((table + ":" + str(index)).encode()).hexdigest()[
                            :14
                        ],
                        16,
                    )
                )
                reg.db.execute(
                    "INSERT INTO source_rows VALUES(?,?,?,?,?,?)",
                    (aid, rowid, cid, confidence, why, json.dumps(redact(raw))),
                )

        try:
            with reg.db:
                if path.suffix == ".csv":
                    with path.open(encoding="utf-8-sig", newline="") as f:
                        consume(csv.DictReader(f))
                else:
                    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                    conn.row_factory = sqlite3.Row
                    try:
                        # Known lead tables only; never reinterpret control/receipt tables as leads.
                        for table in ["leads"]:
                            if conn.execute(
                                'SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',
                                (table,),
                            ).fetchone():
                                consume(conn.execute("SELECT * FROM " + table), table)
                    finally:
                        conn.close()
                reg.db.execute(
                    "INSERT INTO source_artifacts VALUES(?,?,?,?,?,?)",
                    (aid, str(path), digest, "mixed", now(), json.dumps(stats)),
                )
            report["imported_artifacts"] += 1
            report["accepted_rows"] += stats["accepted"]
            report["quarantined_rows"] += stats["quarantined"]
            report["artifacts"].append(stats)
        except (OSError, UnicodeError, csv.Error, sqlite3.Error, ValueError) as exc:
            report["errors"].append({"path": str(path), "error": type(exc).__name__})
    report["research_evidence"] = import_research_evidence(reg, roots)
    if report_path:
        write_json(report_path, report)
    return report


def import_research_evidence(reg, roots):
    """Read supported preserved raw page formats; unsupported JSON stays untouched.

    Receipt/manifest-only assertions do not establish research completion. At least
    one nonempty official-domain page body must be present. Original paths and
    hashes record provenance; no provider client is used.
    """
    from .config import DEFAULTS
    from .extract import extract
    from .pipeline import page_from_payload

    paths = set()
    for root in roots:
        p = Path(root)
        if p.is_dir():
            paths.update(p.rglob("*.json"))
        elif p.suffix == ".json":
            paths.add(p)
    stats = {
        "page_artifacts_imported": 0,
        "page_artifacts_unchanged": 0,
        "unsupported_json": 0,
        "domains_reused": 0,
        "errors": [],
    }
    grouped = {}
    for path in sorted(paths):
        if any(part in path.parts for part in (".venv", ".git", "node_modules")):
            continue
        try:
            data = read_json(path)
            if not isinstance(data, dict):
                stats["unsupported_json"] += 1
                continue
            response = data.get("response") or {}
            request = data.get("request") or {}
            # The formats retained by generalized enrichment and campaign page adapters.
            url = (
                data.get("requested_url")
                or data.get("source_url")
                or data.get("url")
                or request.get("url")
            )
            payload = (
                response.get("provider_response")
                if isinstance(response, dict)
                else None
            )
            success = (
                data.get("status") == "success"
                or data.get("success") is True
                or response.get("success") is True
            )
            if (
                payload is None
                and success
                and (data.get("markdown") or data.get("html"))
            ):
                payload = data
            if payload is None and success and data.get("content"):
                payload = {"html": data["content"]}
            # HTTP envelopes store response content below response.
            if payload is None and success and response.get("content"):
                payload = {"html": response["content"]}
            if (
                payload is None
                or not success
                or not isinstance(url, str)
                or not domain(url)
            ):
                stats["unsupported_json"] += 1
                continue
            d = domain(url)
            if data.get("root_domain") and domain(data["root_domain"]) != d:
                stats["unsupported_json"] += 1
                continue
            timestamp = (
                data.get("requested_at")
                or request.get("requested_at")
                or data.get("scraped_at")
                or data.get("timestamp")
            )
            if not timestamp:
                from datetime import datetime, timezone

                timestamp = datetime.fromtimestamp(
                    data.get("started_at_epoch") or path.stat().st_mtime, timezone.utc
                ).isoformat(timespec="seconds")
            p = page_from_payload(payload, url, str(timestamp), str(path.resolve()))
            if not p:
                stats["unsupported_json"] += 1
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            aid = stable_id("artifact", str(path.resolve()), digest)
            if reg.db.execute(
                "SELECT 1 FROM source_artifacts WHERE artifact_id=?", (aid,)
            ).fetchone():
                stats["page_artifacts_unchanged"] += 1
                continue
            grouped.setdefault(d, []).append((p, aid, digest, str(path.resolve())))
        except (ValueError, TypeError, OSError, AttributeError) as exc:
            stats["errors"].append({"path": str(path), "error": type(exc).__name__})
    for d, items in grouped.items():
        with reg.db:
            cid, _ = reg.ingest(
                {
                    "root_domain": d,
                    "website": "https://" + d,
                    "company_name": d,
                    "timestamp": min(p["timestamp"] for p, _, _, _ in items),
                },
                "history:raw_research",
            )
            if not cid:
                continue
            previous = reg.evidence(d)
            pages = previous["evidence"].get("pages", []) if previous else []
            known = {p["raw_path"] for p in pages}
            pages += [p for p, _, _, _ in items if p["raw_path"] not in known]
            # Empty category deliberately leaves relevance to each future campaign.
            c = {
                **DEFAULTS,
                "business_category": "",
                "max_contacts_per_company": 100,
                "max_emails_per_company": 100,
            }
            evidence = extract(cid, d, pages, c)
            evidence["import_confidence"] = "source_body_present"
            reg.save_research(cid, d, "complete", evidence)
            for p, aid, digest, path in items:
                reg.db.execute(
                    "INSERT INTO source_artifacts VALUES(?,?,?,?,?,?)",
                    (
                        aid,
                        path,
                        digest,
                        "high",
                        now(),
                        json.dumps(
                            {
                                "domain": d,
                                "source_url": p["url"],
                                "type": "raw_research",
                            }
                        ),
                    ),
                )
            stats["page_artifacts_imported"] += len(items)
            stats["domains_reused"] += 1
    return stats
