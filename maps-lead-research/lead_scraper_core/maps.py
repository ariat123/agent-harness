"""Maps discovery and bounded raw-HTTP enrichment."""
from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    FAKE_EMAIL_DOMAIN_BLOCKLIST,
    FAKE_EMAIL_LOCAL_BLOCKLIST,
    RAPIDAPI_ENDPOINT,
    RAPIDAPI_HOST,
    RAPIDAPI_KEY,
    USER_AGENT,
)
from enrichment_utils import norm_email, normalize_url, website_state
from lead_scraper_core.dedupe import exact_keys
from lead_scraper_core.geo import classify_geo, parse_city_state_zip
from lead_scraper_core.normalize import clean, root_domain


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
BAD_EMAIL_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "ico"}
EXTRA_FAKE_EMAIL_LOCALS = {
    "john.doe",
    "jane.doe",
    "first.last",
    "firstname.lastname",
}
EXTRA_FAKE_EMAIL_DOMAINS = {
    "website.com",
    "websitedomain.com",
    "example.org",
}


@dataclass(frozen=True)
class Target:
    search_term: str
    zip: str
    city: str
    state: str
    metro: str
    max_pages: int = 1
    max_results_per_query: int = 10


@dataclass(frozen=True)
class MapsPageResult:
    payload: object | None
    success: bool
    http_status: int | None
    retries: int
    error: str


def parse_int(value: object, default: int) -> int:
    text = clean(value)
    if not text:
        return default
    try:
        return max(1, int(text))
    except ValueError:
        return default


def load_targets(path: Path) -> list[Target]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"search_term", "zip", "city", "state", "metro", "max_pages", "max_results_per_query"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"target CSV missing required columns: {sorted(missing)}")
        targets: list[Target] = []
        for row in reader:
            search_term = clean(row.get("search_term"))
            zip_code = clean(row.get("zip"))
            city = clean(row.get("city"))
            state = clean(row.get("state")).upper()
            metro = clean(row.get("metro"))
            if not search_term or not zip_code or not city or not state or not metro:
                continue
            if not (zip_code.isdigit() and len(zip_code) == 5):
                raise ValueError(f"invalid ZIP in target CSV: {zip_code}")
            targets.append(
                Target(
                    search_term=search_term,
                    zip=zip_code,
                    city=city,
                    state=state,
                    metro=metro,
                    max_pages=parse_int(row.get("max_pages"), 1),
                    max_results_per_query=parse_int(row.get("max_results_per_query"), 10),
                )
            )
    return targets


def request_maps_page(
    query: str,
    page: int,
    *,
    country: str = "us",
    language: str = "en",
    api_key: str | None = None,
    request_get=None,
    sleeper=None,
    max_attempts: int = 4,
) -> MapsPageResult:
    key = api_key if api_key is not None else (os.getenv("RAPIDAPI_KEY") or RAPIDAPI_KEY)
    if not key:
        raise RuntimeError("RAPIDAPI_KEY is required for a live Google Maps request")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    getter = request_get or requests.get
    sleep = sleeper or time.sleep
    headers = {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": RAPIDAPI_HOST,
    }
    params = {
        "query": query,
        "country": country,
        "lang": language,
        "page": str(page),
    }
    last_payload: object | None = None
    last_status: int | None = None
    last_error = "request_failed"
    for attempt in range(max_attempts):
        try:
            response = getter(RAPIDAPI_ENDPOINT, headers=headers, params=params, timeout=30)
        except requests.RequestException as exc:
            last_error = f"network_error:{exc.__class__.__name__}"
            if attempt + 1 < max_attempts:
                sleep(min(30, 2**attempt))
                continue
            return MapsPageResult(None, False, None, attempt, last_error)

        last_status = response.status_code
        try:
            last_payload = response.json()
        except ValueError:
            last_payload = None

        if response.status_code == 200 and last_payload is not None:
            return MapsPageResult(last_payload, True, response.status_code, attempt, "")

        if response.status_code == 200:
            last_error = "non_json_response"
            return MapsPageResult(None, False, response.status_code, attempt, last_error)

        last_error = f"http_{response.status_code}"
        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt + 1 < max_attempts:
            sleep(min(30, 2**attempt))
            continue
        return MapsPageResult(last_payload, False, response.status_code, attempt, last_error)

    return MapsPageResult(last_payload, False, last_status, max_attempts - 1, last_error)


def call_maps(query: str, page: int) -> dict | None:
    try:
        result = request_maps_page(query, page)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return None
    if not result.success:
        print(f"maps request failed for {query!r} page {page}: {result.error}", file=sys.stderr)
        return None
    return result.payload if isinstance(result.payload, dict) else None


def provider_items(payload: object | None) -> list[dict]:
    if not payload:
        return []
    if isinstance(payload, list):
        data = payload
    elif isinstance(payload, dict):
        data = payload.get("data") or payload.get("results") or []
    else:
        return []
    if isinstance(data, dict):
        data = data.get("results", []) or data.get("places", [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def api_items(payload: object | None) -> list[dict]:
    out: list[dict] = []
    for item in provider_items(payload):
        status = clean(
            item.get("business_status")
            or item.get("status")
            or item.get("businessStatus")
            or item.get("temporarily_closed")
        ).lower()
        if any(term in status for term in ("closed", "temporarily")) and "open" not in status:
            continue
        out.append(item)
    return out


def categories_for(item: dict) -> tuple[str, list[str]]:
    categories = item.get("categories") or item.get("types") or item.get("type") or item.get("category")
    if isinstance(categories, str):
        value = clean(categories)
        return value, [value] if value else []
    if isinstance(categories, list):
        values = [clean(category) for category in categories if clean(category)]
        return values[0] if values else "", values
    return "", []


def google_maps_url(place_id: str, item: dict) -> str:
    for key in ("google_maps_url", "maps_url", "place_link", "url", "link"):
        value = clean(item.get(key))
        if value.startswith("http"):
            return value
    if place_id:
        return f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    return ""


def _street_address(address: str, city: str, item: dict) -> str:
    explicit = clean(item.get("street_address") or item.get("address_line_1"))
    if explicit:
        return explicit
    parts = [clean(part) for part in address.split(",") if clean(part)]
    if city:
        for index, part in enumerate(parts):
            if part.casefold() == city.casefold() and index:
                return parts[index - 1]
    return parts[0] if len(parts) >= 2 else address


def normalize_company_item(item: dict) -> dict[str, str]:
    category, categories = categories_for(item)
    website_state_result = website_state(item.get("website") or item.get("site"))
    website = str(website_state_result["website"] or "")
    address = clean(item.get("full_address") or item.get("address") or item.get("formatted_address"))
    city, state, zip_code = parse_city_state_zip(
        address,
        item.get("city"),
        item.get("state"),
        item.get("postal_code") or item.get("zip"),
    )
    place_id = clean(
        item.get("place_id")
        or item.get("placeId")
        or item.get("google_id")
        or item.get("googleId")
        or item.get("id")
    )
    return {
        "place_id": place_id,
        "company_name": clean(item.get("name") or item.get("title")),
        "website": website,
        "root_domain": root_domain(website),
        "phone": clean(item.get("phone_number") or item.get("phone")),
        "address": address,
        "street_address": _street_address(address, city, item),
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "rating": clean(item.get("rating")),
        "review_count": clean(item.get("review_count") or item.get("reviews")),
        "category": category,
        "categories": " | ".join(categories) if categories else category,
        "google_maps_url": google_maps_url(place_id, item),
        "latitude": clean(item.get("latitude") or (item.get("location") or {}).get("lat")),
        "longitude": clean(item.get("longitude") or (item.get("location") or {}).get("lng")),
        "business_status": clean(
            item.get("business_status")
            or item.get("status")
            or item.get("businessStatus")
            or item.get("temporarily_closed")
        ),
    }


def normalize_item(item: dict, target: Target, profile, query_used: str, page: int, all_target_zips: set[str]) -> dict[str, str]:
    company = normalize_company_item(item)
    row = {
        "place_id": company["place_id"],
        "company_name": company["company_name"],
        "website": company["website"],
        "root_domain": company["root_domain"],
        "phone": company["phone"],
        "email": "",
        "address": company["address"],
        "city": company["city"],
        "state": company["state"],
        "zip_code": company["zip_code"],
        "rating": company["rating"],
        "review_count": company["review_count"],
        "categories": company["categories"],
        "query_used": query_used,
        "source_zip": target.zip,
        "source_city": target.city,
        "source_state": target.state,
        "source_metro": target.metro,
        "geo_status": "",
        "geo_rejection_reason": "",
        "notes": f"maps page {page}",
        "google_maps_url": company["google_maps_url"],
        "latitude": company["latitude"],
        "longitude": company["longitude"],
        "business_status": company["business_status"],
        "source_page": "",
        "fit_status": "",
        "fit_label": "",
        "fit_reason": "",
        "source_run_id": "",
    }
    row["geo_status"], row["geo_rejection_reason"] = classify_geo(row, target, profile, all_target_zips)
    return row


def is_fake_email(email: str) -> bool:
    if "@" not in email:
        return True
    local, domain = email.lower().rsplit("@", 1)
    tld = domain.rsplit(".", 1)[-1]
    return (
        tld in BAD_EMAIL_TLDS
        or local in FAKE_EMAIL_LOCAL_BLOCKLIST
        or local in EXTRA_FAKE_EMAIL_LOCALS
        or domain in FAKE_EMAIL_DOMAIN_BLOCKLIST
        or domain in EXTRA_FAKE_EMAIL_DOMAINS
    )


def extract_emails(markup: str) -> list[str]:
    soup = BeautifulSoup(markup, "html.parser")
    emails: list[str] = []
    for tag in soup.select("a[href^='mailto:']"):
        email = norm_email(tag.get("href"))
        if email and not is_fake_email(email) and email not in emails:
            emails.append(email)
    for match in EMAIL_RE.findall(html.unescape(markup)):
        email = norm_email(match)
        if email and not is_fake_email(email) and email not in emails:
            emails.append(email)
    return emails


def email_score(email: str, root: str) -> tuple[int, int]:
    if "@" not in email:
        return (-1000, 0)
    local, domain = email.lower().rsplit("@", 1)
    generic = {
        "info",
        "hello",
        "contact",
        "office",
        "admin",
        "support",
        "leasing",
        "rentals",
        "management",
        "propertymanagement",
        "pm",
    }
    score = 0
    if root and (domain == root or domain.endswith("." + root)):
        score += 100
    if local in generic:
        score += 20
    else:
        score += 35
    return (score, -len(email))


def pick_email(emails: list[str], root: str) -> str:
    if not emails:
        return ""
    return sorted(emails, key=lambda email: email_score(email, root), reverse=True)[0]


def same_site_url(base: str, href: str) -> str:
    if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return ""
    absolute = normalize_url(urljoin(base, href))
    if not absolute:
        return ""
    base_host = urlparse(base).netloc.lower().removeprefix("www.")
    host = urlparse(absolute).netloc.lower().removeprefix("www.")
    return absolute if host == base_host else ""


def fetch_url(session: requests.Session, url: str) -> str:
    response = None
    chunks: list[bytes] = []
    deadline = time.monotonic() + 4
    try:
        response = session.get(url, timeout=(3, 3), allow_redirects=True, stream=True)
        response.raise_for_status()
        ctype = response.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype and ctype:
            return ""
        total = 0
        for chunk in response.iter_content(chunk_size=16_384):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= 300_000 or time.monotonic() > deadline:
                break
    except requests.RequestException:
        return ""
    finally:
        if response is not None:
            response.close()
    return b"".join(chunks).decode(response.encoding or "utf-8", errors="ignore") if chunks else ""


def contact_links(base: str, markup: str, profile) -> list[str]:
    soup = BeautifulSoup(markup, "html.parser")
    links: list[str] = []
    terms = tuple(profile.RAW_ENRICHMENT_LINK_TERMS)
    for tag in soup.select("a[href]"):
        href = tag.get("href") or ""
        text = clean(tag.get_text(" ", strip=True)).lower()
        haystack = f"{href} {text}".lower()
        if not any(term in haystack for term in terms):
            continue
        same_site = same_site_url(base, href)
        if same_site and same_site not in links:
            links.append(same_site)
    return links[: profile.RAW_ENRICHMENT_MAX_LINKS]


def enrich_email(row: dict[str, str], session: requests.Session, profile) -> None:
    website = normalize_url(row.get("website"))
    if not website:
        return
    root = row.get("root_domain", "")
    homepage = fetch_url(session, website)
    if not homepage:
        return
    pages = [(website, homepage)]
    for link in contact_links(website, homepage, profile):
        markup = fetch_url(session, link)
        if markup:
            pages.append((link, markup))
    emails: list[str] = []
    for page_url, markup in pages:
        for email in extract_emails(markup):
            if email not in emails:
                emails.append(email)
        if emails and not row.get("source_page"):
            row["source_page"] = page_url
    row["email"] = pick_email(emails, root)


def discover(
    profile,
    targets: list[Target],
    query_mode: str,
    count: int | None,
    enrich: bool,
) -> tuple[list[dict[str, str]], int, set[str], list[dict[str, object]]]:
    raw_rows: list[dict[str, str]] = []
    maps_calls = 0
    queried_zips: set[str] = set()
    target_stats: list[dict[str, object]] = []
    all_target_zips = {target.zip for target in targets}
    accepted_count = 0
    seen: set[tuple[str, str]] = set()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    for target in targets:
        query = profile.query_for(target, query_mode)
        stats: dict[str, object] = {
            "search_term": target.search_term,
            "zip": target.zip,
            "city": target.city,
            "state": target.state,
            "metro": target.metro,
            "query_used": query,
            "maps_page_calls": 0,
            "raw_rows": 0,
            "in_geo_rows": 0,
            "status": "completed",
        }
        queried_zips.add(target.zip)
        retained_for_target = 0
        for page in range(1, target.max_pages + 1):
            if retained_for_target >= target.max_results_per_query:
                break
            payload = call_maps(query, page)
            maps_calls += 1
            stats["maps_page_calls"] = int(stats["maps_page_calls"]) + 1
            items = api_items(payload)
            print(f"{query} page {page}: {len(items)} rows", flush=True)
            if not items:
                break
            for item in items:
                if retained_for_target >= target.max_results_per_query:
                    break
                row = normalize_item(item, target, profile, query, page, all_target_zips)
                keys = exact_keys(row)
                if keys and keys & seen:
                    continue
                seen.update(keys)
                retained_for_target += 1

                in_geo = row["geo_status"] in {"in_target_zip", "nearby_target_metro", "in_target_state"}
                if in_geo:
                    accepted_count += 1
                    stats["in_geo_rows"] = int(stats["in_geo_rows"]) + 1

                if enrich and row.get("website") and row["geo_status"] != "out_of_geo":
                    enrich_email(row, session, profile)
                row["fit_status"], row["fit_label"], row["fit_reason"] = profile.classify_fit(row)
                raw_rows.append(row)
                stats["raw_rows"] = int(stats["raw_rows"]) + 1
                if count is not None and accepted_count >= count:
                    stats["status"] = "stopped_count_reached"
                    target_stats.append(stats)
                    return raw_rows, maps_calls, queried_zips, target_stats
            if len(items) < 10:
                break
        target_stats.append(stats)
    return raw_rows, maps_calls, queried_zips, target_stats
