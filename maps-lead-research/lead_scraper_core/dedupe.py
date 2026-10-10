"""Generic dedupe keys for normalized lead rows."""
from __future__ import annotations

from lead_scraper_core.normalize import address_key, clean, normalize_company_name, normalize_phone


def exact_keys(row: dict[str, str]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    place_id = clean(row.get("place_id"))
    root = clean(row.get("root_domain")).lower()
    address = address_key(row.get("address"))
    name = normalize_company_name(row.get("company_name"))
    phone = normalize_phone(row.get("phone"))
    if place_id:
        keys.add(("place_id", place_id))
    if root and address:
        keys.add(("root_address", f"{root}|{address}"))
    if root and name:
        keys.add(("root_name", f"{root}|{name}"))
    if phone and name:
        keys.add(("phone_name", f"{phone}|{name}"))
    return keys


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        keys = exact_keys(row)
        if keys and keys & seen:
            continue
        seen.update(keys)
        out.append(row)
    return out

