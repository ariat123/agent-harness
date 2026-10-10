"""Normalization helpers shared by scraper stages."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from enrichment_utils import norm_email, normalize_key, normalize_url, root_domain as root_for_host


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text or "run"


def normalize_company_name(value: object) -> str:
    text = clean(value).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_phone(value: object) -> str:
    digits = re.sub(r"\D+", "", clean(value))
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def normalize_email(value: object) -> str:
    text = clean(value).lower()
    if not text:
        return ""
    email = norm_email(text)
    return email or text


def root_domain(value: object) -> str:
    normalized = normalize_url(clean(value))
    if not normalized:
        return ""
    host = urlparse(normalized).netloc.lower().removeprefix("www.")
    return root_for_host(host)


def normalize_domain(value: object) -> str:
    text = clean(value).lower()
    if not text:
        return ""
    if "://" in text or "/" in text:
        return root_domain(text)
    return text.removeprefix("www.").strip(" .,/").lower()


def address_key(value: object) -> str:
    return normalize_key(value)

