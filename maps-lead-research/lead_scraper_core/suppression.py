"""Suppression loading and matching."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from lead_scraper_core.normalize import (
    clean,
    normalize_company_name,
    normalize_domain,
    normalize_email,
    normalize_phone,
)


@dataclass
class SuppressionMatch:
    field: str
    value: str
    status: str
    reason: str


@dataclass
class SuppressionData:
    loaded: bool = False
    path: str = ""
    row_count: int = 0
    domains: dict[str, dict[str, str]] | None = None
    company_names: dict[str, dict[str, str]] | None = None
    emails: dict[str, dict[str, str]] | None = None
    phones: dict[str, dict[str, str]] | None = None

    def __post_init__(self) -> None:
        self.domains = self.domains or {}
        self.company_names = self.company_names or {}
        self.emails = self.emails or {}
        self.phones = self.phones or {}

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


def suppression_record(row: dict[str, str]) -> dict[str, str]:
    return {
        "company_name": clean(row.get("company_name")),
        "normalized_company_name": normalize_company_name(row.get("normalized_company_name") or row.get("company_name")),
        "root_domain": normalize_domain(row.get("root_domain") or row.get("website")),
        "email": normalize_email(row.get("email")),
        "phone": normalize_phone(row.get("phone")),
        "website": clean(row.get("website")),
        "city": clean(row.get("city")),
        "state": clean(row.get("state")).upper(),
        "status": clean(row.get("status")),
        "status_reason": clean(row.get("status_reason")),
        "source_file": clean(row.get("source_file")),
        "suppression_notes": clean(row.get("suppression_notes")),
    }


def load_suppression_master(path: str | None) -> SuppressionData:
    if not path:
        return SuppressionData(loaded=False)
    master_path = Path(path)
    if not master_path.exists():
        raise FileNotFoundError(f"suppression master not found: {master_path}")

    data = SuppressionData(loaded=True, path=str(master_path))
    with master_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "company_name",
            "normalized_company_name",
            "root_domain",
            "email",
            "phone",
            "website",
            "city",
            "state",
            "status",
            "status_reason",
            "source_file",
            "suppression_notes",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"suppression master missing required columns: {sorted(missing)}")
        for row in reader:
            data.row_count += 1
            record = suppression_record(row)
            if record["root_domain"]:
                data.domains.setdefault(record["root_domain"], record)
            if record["normalized_company_name"]:
                data.company_names.setdefault(record["normalized_company_name"], record)
            if record["email"]:
                data.emails.setdefault(record["email"], record)
            if record["phone"]:
                data.phones.setdefault(record["phone"], record)
    return data


def suppression_reason(record: dict[str, str]) -> str:
    parts = [
        record.get("status_reason", ""),
        record.get("suppression_notes", ""),
        record.get("source_file", ""),
    ]
    return " | ".join(part for part in parts if part)


def match_suppression(row: dict[str, str], suppression: SuppressionData) -> SuppressionMatch | None:
    if not suppression.loaded:
        return None
    checks = [
        ("root_domain", normalize_domain(row.get("root_domain") or row.get("website")), suppression.domains),
        ("normalized_company_name", normalize_company_name(row.get("company_name")), suppression.company_names),
        ("email", normalize_email(row.get("email")), suppression.emails),
        ("phone", normalize_phone(row.get("phone")), suppression.phones),
    ]
    for field, value, lookup in checks:
        if not value or not lookup:
            continue
        record = lookup.get(value)
        if not record:
            continue
        return SuppressionMatch(
            field=field,
            value=value,
            status=record.get("status", ""),
            reason=suppression_reason(record),
        )
    return None


def suppressed_row(row: dict[str, str], match: SuppressionMatch) -> dict[str, str]:
    return {
        **row,
        "suppression_reason": match.reason,
        "matched_suppression_field": match.field,
        "matched_suppression_value": match.value,
        "matched_suppression_status": match.status,
    }

