"""Generic profile-based gap-fill enrichment for already discovered leads."""
from __future__ import annotations

import csv
import html
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import USER_AGENT
from enrichment_utils import normalize_url
from lead_scraper_core.firecrawl import (
    FirecrawlApp,
    firecrawl_api_key,
    response_credits,
    scrape_with_retries,
    url_for_path,
)
from lead_scraper_core.normalize import clean, normalize_domain, normalize_email
from lead_scraper_core.outputs import append_csv_row, ensure_csv_header, write_csv


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
DEFAULT_CONNECT_TIMEOUT = 2
DEFAULT_READ_TIMEOUT = 6
DEFAULT_SEARCH_RESULTS = 5
DEFAULT_TARGET_PATHS = ["", "about", "about-us", "team", "contact"]

GENERIC_EMAIL_LOCALS = {
    "admin",
    "billing",
    "contact",
    "hello",
    "help",
    "info",
    "leasing",
    "mail",
    "management",
    "office",
    "sales",
    "service",
    "support",
    "team",
}

BAD_NAME_PARTS = {
    "about",
    "association",
    "brokerage",
    "business",
    "california",
    "commercial",
    "company",
    "contact",
    "estate",
    "home",
    "homes",
    "leasing",
    "management",
    "manager",
    "property",
    "properties",
    "real",
    "rental",
    "rentals",
    "service",
    "services",
    "team",
}


GAP_EXTRA_FIELDS = [
    "gap_fill_requested",
    "gap_fill_status",
    "gap_filled_fields",
    "gap_fill_sources",
    "gap_decision_makers",
    "gap_decision_maker_source",
    "gap_decision_maker_confidence",
    "gap_decision_maker_evidence",
    "gap_email_status",
    "gap_email_source",
    "gap_email",
    "inferred_email",
    "inferred_email_confidence",
    "inferred_email_reason",
    "gap_raw_source_urls",
    "gap_web_search_source_urls",
    "gap_firecrawl_status",
    "gap_firecrawl_credits_used",
    "gap_firecrawl_pages_attempted",
    "gap_firecrawl_source_pages",
    "gap_firecrawl_error",
    "gap_firecrawl_json",
    "gap_firecrawl_strategy",
    "gap_discovery_source_urls",
    "linkedin_candidate_urls",
]

GAP_QUEUE_EXTRA_FIELDS = [
    "gap_queue_status",
    "gap_queue_reason",
    "gap_skip_reason",
]

GAP_CANDIDATE_FIELDS = [
    "place_id",
    "company_name",
    "website",
    "root_domain",
    "city",
    "state",
    "zip_code",
    "gap",
    "candidate_type",
    "candidate_value",
    "candidate_name",
    "candidate_role",
    "candidate_email",
    "candidate_phone",
    "candidate_confidence",
    "candidate_source",
    "candidate_source_url",
    "candidate_evidence",
    "candidate_status",
]

GAP_FAILED_FIELDS = GAP_CANDIDATE_FIELDS + [
    "failure_reason",
    "firecrawl_error",
]


@dataclass
class GapCandidate:
    candidate_type: str
    value: str
    source: str
    source_url: str = ""
    confidence: str = "medium"
    evidence: str = ""
    name: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""
    inferred: bool = False
    inferred_reason: str = ""


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def csv_fields(path: Path, rows: list[dict[str, str]] | None = None) -> list[str]:
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return list(reader.fieldnames or [])
    out: list[str] = []
    for row in rows or []:
        for key in row:
            if key not in out:
                out.append(key)
    return out


def output_fields(input_fields: list[str]) -> list[str]:
    fields = list(input_fields)
    for field in GAP_EXTRA_FIELDS:
        if field not in fields:
            fields.append(field)
    return fields


def queue_fields(input_fields: list[str]) -> list[str]:
    fields = output_fields(input_fields)
    for field in GAP_QUEUE_EXTRA_FIELDS:
        if field not in fields:
            fields.append(field)
    return fields


def split_multi_value(value: object) -> list[str]:
    text = clean(value)
    if not text:
        return []
    parts = re.split(r"\s*(?:\||,|;)\s*", text)
    return [part for part in (clean(item) for item in parts) if part]


def unique_strings(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean(value)
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def append_unique(existing: str, additions: Iterable[object]) -> str:
    return " | ".join(unique_strings([*split_multi_value(existing), *additions]))


def extract_emails(text: object) -> list[str]:
    emails: list[str] = []
    for email in EMAIL_RE.findall(str(text or "")):
        normalized = normalize_email(email.strip(" .,:;()[]{}<>\"'"))
        if normalized and normalized not in emails:
            emails.append(normalized)
    return emails


def has_email(row: dict[str, str]) -> bool:
    fields = ["email", "firecrawl_emails", "gap_email", "inferred_email"]
    return any(extract_emails(row.get(field)) for field in fields)


def has_confirmed_email(row: dict[str, str]) -> bool:
    return any(extract_emails(row.get(field)) for field in ["email", "firecrawl_emails", "gap_email"])


def has_decision_maker(row: dict[str, str]) -> bool:
    return bool(clean(row.get("decision_makers")) or clean(row.get("gap_decision_makers")))


def is_missing_gap(row: dict[str, str], gap: str) -> bool:
    if gap == "decision_makers":
        return not has_decision_maker(row)
    if gap == "emails":
        return not has_email(row)
    if gap == "all":
        return not has_decision_maker(row) or not has_email(row)
    raise ValueError(f"unknown gap: {gap}")


def missing_rows(rows: list[dict[str, str]], gap: str) -> list[dict[str, str]]:
    return [row for row in rows if is_missing_gap(row, gap)]


def identity_key(row: dict[str, str]) -> tuple[str, str]:
    domain = normalize_domain(row.get("root_domain") or row.get("website"))
    if domain:
        return "domain", domain
    place_id = clean(row.get("place_id")).lower()
    if place_id:
        return "place_id", place_id
    company = clean(row.get("company_name")).lower()
    city = clean(row.get("city")).lower()
    state = clean(row.get("state")).lower()
    return "company_geo", f"{company}|{city}|{state}"


def candidate_key(candidate: GapCandidate) -> tuple[str, str, str]:
    return (
        candidate.candidate_type,
        clean(candidate.value).lower(),
        clean(candidate.source_url).lower(),
    )


def candidate_to_row(row: dict[str, str], gap: str, candidate: GapCandidate, status: str = "candidate") -> dict[str, str]:
    return {
        "place_id": row.get("place_id", ""),
        "company_name": row.get("company_name", ""),
        "website": row.get("website", ""),
        "root_domain": row.get("root_domain", ""),
        "city": row.get("city", ""),
        "state": row.get("state", ""),
        "zip_code": row.get("zip_code", ""),
        "gap": gap,
        "candidate_type": candidate.candidate_type,
        "candidate_value": candidate.value,
        "candidate_name": candidate.name,
        "candidate_role": candidate.role,
        "candidate_email": candidate.email,
        "candidate_phone": candidate.phone,
        "candidate_confidence": candidate.confidence,
        "candidate_source": candidate.source,
        "candidate_source_url": candidate.source_url,
        "candidate_evidence": candidate.evidence,
        "candidate_status": status,
    }


def clean_person_name(value: object) -> str:
    text = clean(value)
    text = re.sub(r"^(meet|contact|about|email|call)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(linkedin|profile|email|phone)$", "", text, flags=re.I)
    return clean(text.strip(" -–—,:;|/"))


def valid_person_name(value: object) -> bool:
    name = clean_person_name(value)
    parts = [part.strip(".,") for part in name.split()]
    if len(parts) < 2 or len(parts) > 4:
        return False
    lowered = {re.sub(r"[^a-z]", "", part.lower()) for part in parts}
    if not lowered or lowered & BAD_NAME_PARTS:
        return False
    for part in parts:
        if not re.fullmatch(r"[A-Z][A-Za-z'.-]+|[A-Z]\.?", part):
            return False
    return True


def role_pattern(roles: list[str]) -> str:
    escaped = [re.escape(role) for role in sorted(roles, key=len, reverse=True)]
    return r"(?:" + "|".join(escaped) + r")"


def name_pattern() -> str:
    return r"[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,3}"


def visible_text(markup: str) -> str:
    soup = BeautifulSoup(markup or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return html.unescape(soup.get_text(" | ", strip=True))


def mailto_emails(markup: str) -> list[str]:
    soup = BeautifulSoup(markup or "", "html.parser")
    emails: list[str] = []
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "")
        if href.lower().startswith("mailto:"):
            emails.extend(extract_emails(href))
    return unique_strings(emails)


def extract_role_contacts(text: str, roles: list[str], source: str, source_url: str) -> list[GapCandidate]:
    if not text:
        return []
    role_re = role_pattern(roles)
    name_re = name_pattern()
    patterns = [
        re.compile(rf"\b(?P<name>{name_re})\s*(?:[-–—,|]| is |,?\s+)\s*(?P<role>{role_re})\b", re.I),
        re.compile(rf"\b(?P<role>{role_re})\s*(?:[-–—,:|]|\s+)\s*(?P<name>{name_re})\b", re.I),
    ]
    candidates: list[GapCandidate] = []
    for pattern in patterns:
        for match in pattern.finditer(text[:250_000]):
            name = clean_person_name(match.group("name"))
            if not valid_person_name(name):
                continue
            role = clean(match.group("role")).lower()
            start = max(match.start() - 120, 0)
            end = min(match.end() + 160, len(text))
            evidence = clean(text[start:end])
            nearby = text[start:end]
            emails = extract_emails(nearby)
            phones = PHONE_RE.findall(nearby)
            candidates.append(
                GapCandidate(
                    candidate_type="decision_maker",
                    value=f"{name} / {role}",
                    source=source,
                    source_url=source_url,
                    confidence="medium",
                    evidence=evidence,
                    name=name,
                    role=role,
                    email=emails[0] if emails else "",
                    phone=phones[0] if phones else "",
                )
            )
    return dedupe_candidates(candidates)


def dedupe_candidates(candidates: list[GapCandidate]) -> list[GapCandidate]:
    out: list[GapCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        key = candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def decision_roles(profile) -> list[str]:
    roles = list(
        getattr(
            profile,
            "GAP_FILL_DECISION_MAKER_ROLES",
            getattr(profile, "DECISION_MAKER_ROLES", []),
        )
    )
    return roles or ["owner", "founder", "president", "principal", "manager"]


def target_paths(profile) -> list[str]:
    paths = list(getattr(profile, "GAP_FILL_TARGET_PATHS", []))
    if paths:
        return paths
    config = getattr(profile, "FIRECRAWL", {})
    return list(config.get("paths") or DEFAULT_TARGET_PATHS)


def root_domain_for_row(row: dict[str, str]) -> str:
    return normalize_domain(row.get("root_domain") or row.get("website"))


def same_domain_email(email: str, root_domain: str) -> bool:
    if not email or "@" not in email or not root_domain:
        return False
    return normalize_domain(email.split("@", 1)[1]) == root_domain


def person_email_pattern(email: str) -> tuple[str, str] | None:
    local = email.split("@", 1)[0].lower()
    if local in GENERIC_EMAIL_LOCALS or any(char.isdigit() for char in local):
        return None
    if re.fullmatch(r"[a-z]+[._-][a-z]+", local):
        separator = re.search(r"[._-]", local)
        return "first.last", separator.group(0) if separator else "."
    if re.fullmatch(r"[a-z][a-z]+", local):
        return "first", ""
    if re.fullmatch(r"[a-z][a-z]+[._-]?[a-z]", local):
        return "unknown_person_local", ""
    return None


def infer_email_from_pattern(name: str, root_domain: str, known_emails: list[str]) -> GapCandidate | None:
    if not valid_person_name(name) or not root_domain:
        return None
    first, last = [part.lower().strip(".,") for part in name.split()[:2]]
    domain_emails = [email for email in known_emails if same_domain_email(email, root_domain)]
    pattern_counts: Counter[str] = Counter()
    separators: Counter[str] = Counter()
    for email in domain_emails:
        pattern = person_email_pattern(email)
        if not pattern:
            continue
        pattern_counts[pattern[0]] += 1
        if pattern[1]:
            separators[pattern[1]] += 1
    if pattern_counts["first.last"] > 0:
        sep = separators.most_common(1)[0][0] if separators else "."
        inferred = f"{first}{sep}{last}@{root_domain}"
        reason = f"Observed same-domain person email pattern first{sep}last"
        confidence = "high"
    else:
        inferred = f"{first}.{last}@{root_domain}"
        reason = "No confirmed same-domain person pattern; common first.last guess"
        confidence = "low"
    return GapCandidate(
        candidate_type="email",
        value=inferred,
        source="email_inference",
        confidence=confidence,
        email=inferred,
        inferred=True,
        inferred_reason=reason,
        evidence=reason,
    )


def parse_firecrawl_json(value: object) -> list[dict[str, object]]:
    text = clean(value)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def candidates_from_firecrawl_json(profile, row: dict[str, str], gap: str) -> list[GapCandidate]:
    page_results = parse_firecrawl_json(row.get("firecrawl_json"))
    if not page_results:
        return []
    if hasattr(profile, "gap_fill_candidates_from_firecrawl"):
        return list(profile.gap_fill_candidates_from_firecrawl(row, page_results, gap))
    if hasattr(profile, "flatten_firecrawl_data"):
        merged = profile.flatten_firecrawl_data(page_results)
        candidates: list[GapCandidate] = []
        for contact in merged.get("decision_contacts", []):
            if not isinstance(contact, dict):
                continue
            name = clean_person_name(contact.get("full_name"))
            role = clean(contact.get("role"))
            if not name or not valid_person_name(name):
                continue
            email = normalize_email(contact.get("email"))
            phone = clean(contact.get("phone"))
            candidates.append(
                GapCandidate(
                    candidate_type="decision_maker",
                    value=f"{name} / {role}".strip(" /"),
                    source="existing_firecrawl_json",
                    confidence=clean(contact.get("confidence")) or "medium",
                    evidence=clean(contact.get("notes")) or "Existing Firecrawl JSON decision contact",
                    name=name,
                    role=role,
                    email=email,
                    phone=phone,
                )
            )
        for email in merged.get("business_emails", []):
            normalized = normalize_email(email)
            if normalized:
                candidates.append(
                    GapCandidate(
                        candidate_type="email",
                        value=normalized,
                        source="existing_firecrawl_json",
                        confidence="high",
                        email=normalized,
                        evidence="Existing Firecrawl JSON business email",
                    )
                )
        return dedupe_candidates(candidates)
    return []


def fetch_url(session: requests.Session, url: str) -> tuple[str, str]:
    normalized = normalize_url(url)
    if not normalized:
        return "", ""
    try:
        response = session.get(
            normalized,
            headers={"User-Agent": USER_AGENT},
            timeout=(DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
            allow_redirects=True,
        )
    except requests.RequestException:
        return normalized, ""
    if response.status_code >= 400:
        return response.url, ""
    content_type = response.headers.get("content-type", "")
    if content_type and "text" not in content_type and "html" not in content_type:
        return response.url, ""
    return response.url, response.text[:500_000]


def raw_website_candidates(
    profile,
    row: dict[str, str],
    gap: str,
    session: requests.Session | None = None,
    max_pages: int | None = None,
) -> list[GapCandidate]:
    base = normalize_url(row.get("website") or "")
    if not base:
        return []
    session = session or requests.Session()
    candidates: list[GapCandidate] = []
    roles = decision_roles(profile)
    page_limit = max_pages if max_pages is not None else int(getattr(profile, "GAP_FILL_MAX_RAW_PAGES", 5))
    for path in target_paths(profile)[:page_limit]:
        url = url_for_path(base, path)
        final_url, markup = fetch_url(session, url)
        if not markup:
            continue
        text = visible_text(markup)
        for email in unique_strings([*extract_emails(text), *mailto_emails(markup)]):
            candidates.append(
                GapCandidate(
                    candidate_type="email",
                    value=email,
                    source="raw_website",
                    source_url=final_url,
                    confidence="high",
                    email=email,
                    evidence="Visible email or mailto link on company website",
                )
            )
        candidates.extend(extract_role_contacts(text, roles, "raw_website", final_url))
    return dedupe_candidates(candidates)


def yahoo_unwrap(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if "r.search.yahoo.com" not in parsed.netloc:
        return url
    match = re.search(r"/RU=([^/]+?)/(?:RK|RS)=", url)
    if match:
        return unquote(match.group(1))
    qs = parse_qs(parsed.query)
    for key in ("RU", "url"):
        if qs.get(key):
            return unquote(qs[key][0])
    return url


def default_search_queries(profile, row: dict[str, str]) -> list[str]:
    if hasattr(profile, "gap_fill_search_queries"):
        return list(profile.gap_fill_search_queries(row))
    company = clean(row.get("company_name"))
    domain = root_domain_for_row(row)
    roles = decision_roles(profile)[:4]
    queries = [f'"{company}" "{role}"' for role in roles if company]
    if domain:
        queries.append(f'"{domain}" owner founder president')
    return unique_strings(queries)


def html_search_results(query: str, session: requests.Session | None = None) -> list[dict[str, str]]:
    session = session or requests.Session()
    url = f"https://search.yahoo.com/search?p={quote_plus(query)}"
    try:
        response = session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=(DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
        )
    except requests.RequestException:
        return []
    if response.status_code >= 400:
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict[str, str]] = []
    for item in soup.select(".dd.algo"):
        link = item.find("a", href=True)
        title = clean(link.get_text(" ", strip=True)) if link else ""
        result_url = yahoo_unwrap(link.get("href", "")) if link else ""
        snippet = clean(item.get_text(" ", strip=True))
        if title or snippet:
            results.append({"title": title, "snippet": snippet, "url": result_url, "query": query})
        if len(results) >= DEFAULT_SEARCH_RESULTS:
            break
    return results


def result_matches_row(row: dict[str, str], result: dict[str, str]) -> bool:
    blob = f"{result.get('title', '')} {result.get('snippet', '')} {result.get('url', '')}".lower()
    domain = root_domain_for_row(row)
    if domain and domain in blob:
        return True
    company_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", clean(row.get("company_name")).lower())
        if token not in BAD_NAME_PARTS and len(token) > 2
    ]
    if company_tokens and sum(1 for token in company_tokens if token in blob) >= min(2, len(company_tokens)):
        return True
    return False


def web_search_candidates(
    profile,
    row: dict[str, str],
    gap: str,
    session: requests.Session | None = None,
    max_queries: int | None = None,
) -> list[GapCandidate]:
    session = session or requests.Session()
    candidates: list[GapCandidate] = []
    roles = decision_roles(profile)
    queries = default_search_queries(profile, row)
    if max_queries is not None:
        queries = queries[:max_queries]
    for query in queries:
        for result in html_search_results(query, session=session):
            if not result_matches_row(row, result):
                continue
            source_url = result.get("url", "")
            source = "linkedin_search" if "linkedin.com" in source_url.lower() else "web_search"
            text = f"{result.get('title', '')} {result.get('snippet', '')}"
            candidates.extend(extract_role_contacts(text, roles, source, source_url))
            for email in extract_emails(text):
                candidates.append(
                    GapCandidate(
                        candidate_type="email",
                        value=email,
                        source=source,
                        source_url=source_url,
                        confidence="medium",
                        email=email,
                        evidence=clean(text),
                    )
                )
    return dedupe_candidates(candidates)


def apply_candidates_to_row(row: dict[str, str], candidates: list[GapCandidate], requested_gap: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    updated = dict(row)
    accepted: list[dict[str, str]] = []
    filled_fields: list[str] = []
    sources: list[str] = []
    root = root_domain_for_row(updated)
    known_emails = []
    for field in ["email", "firecrawl_emails", "gap_email"]:
        known_emails.extend(extract_emails(updated.get(field)))

    decision_candidates = [c for c in candidates if c.candidate_type == "decision_maker" and c.name]
    email_candidates = [c for c in candidates if c.candidate_type == "email" and c.email]

    if not has_decision_maker(updated) and decision_candidates:
        best = sorted(
            decision_candidates,
            key=lambda c: {"high": 0, "medium": 1, "low": 2}.get(c.confidence, 1),
        )[0]
        updated["decision_makers"] = best.value
        updated["gap_decision_makers"] = best.value
        updated["gap_decision_maker_source"] = best.source
        updated["gap_decision_maker_confidence"] = best.confidence
        updated["gap_decision_maker_evidence"] = best.evidence
        accepted.append(candidate_to_row(updated, requested_gap, best, "accepted"))
        filled_fields.append("decision_makers")
        sources.append(best.source)
        if best.email and not has_confirmed_email(updated):
            email_candidates.insert(
                0,
                GapCandidate(
                    candidate_type="email",
                    value=best.email,
                    source=best.source,
                    source_url=best.source_url,
                    confidence=best.confidence,
                    email=best.email,
                    evidence=f"Email found with decision maker {best.name}",
                ),
            )
        inferred = infer_email_from_pattern(best.name, root, known_emails)
        if inferred:
            email_candidates.append(inferred)

    confirmed_email_candidates = [
        c for c in email_candidates
        if c.email and not c.inferred and (not root or same_domain_email(c.email, root) or c.source in {"existing_firecrawl_json", "raw_website"})
    ]
    high_conf_inferred = [c for c in email_candidates if c.inferred and c.confidence == "high"]
    if not has_confirmed_email(updated) and confirmed_email_candidates:
        best_email = sorted(
            confirmed_email_candidates,
            key=lambda c: {"high": 0, "medium": 1, "low": 2}.get(c.confidence, 1),
        )[0]
        updated["email"] = best_email.email
        updated["gap_email"] = best_email.email
        updated["gap_email_status"] = "confirmed"
        updated["gap_email_source"] = best_email.source
        accepted.append(candidate_to_row(updated, requested_gap, best_email, "accepted"))
        filled_fields.append("email")
        sources.append(best_email.source)
    elif not has_confirmed_email(updated) and high_conf_inferred:
        inferred = high_conf_inferred[0]
        updated["email"] = inferred.email
        updated["gap_email"] = inferred.email
        updated["gap_email_status"] = "inferred_high_confidence"
        updated["gap_email_source"] = inferred.source
        updated["inferred_email"] = inferred.email
        updated["inferred_email_confidence"] = inferred.confidence
        updated["inferred_email_reason"] = inferred.inferred_reason
        accepted.append(candidate_to_row(updated, requested_gap, inferred, "accepted"))
        filled_fields.append("email")
        sources.append(inferred.source)
    else:
        low_inferred = [c for c in email_candidates if c.inferred]
        if low_inferred and not clean(updated.get("inferred_email")):
            inferred = low_inferred[0]
            updated["inferred_email"] = inferred.email
            updated["inferred_email_confidence"] = inferred.confidence
            updated["inferred_email_reason"] = inferred.inferred_reason

    linkedin_urls = [c.source_url for c in candidates if "linkedin.com" in clean(c.source_url).lower()]
    if linkedin_urls:
        updated["linkedin_candidate_urls"] = append_unique(updated.get("linkedin_candidate_urls"), linkedin_urls)

    if filled_fields:
        updated["gap_fill_requested"] = requested_gap
        updated["gap_fill_status"] = "filled"
        updated["gap_filled_fields"] = append_unique(updated.get("gap_filled_fields"), filled_fields)
        updated["gap_fill_sources"] = append_unique(updated.get("gap_fill_sources"), sources)
    return updated, accepted


def existing_data_updates(profile, rows: list[dict[str, str]], gap: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    updated_rows: list[dict[str, str]] = []
    accepted_rows: list[dict[str, str]] = []
    for row in rows:
        candidates = candidates_from_firecrawl_json(profile, row, gap)
        updated, accepted = apply_candidates_to_row(row, candidates, gap)
        updated_rows.append(updated)
        accepted_rows.extend(accepted)
    return updated_rows, accepted_rows


def raw_website_updates(
    profile,
    rows: list[dict[str, str]],
    gap: str,
    max_pages: int | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    session = requests.Session()
    session.max_redirects = 3
    updated_rows: list[dict[str, str]] = []
    candidate_rows: list[dict[str, str]] = []
    for row in rows:
        if not is_missing_gap(row, gap):
            updated_rows.append(row)
            continue
        candidates = raw_website_candidates(profile, row, gap, session=session, max_pages=max_pages)
        for candidate in candidates:
            candidate_rows.append(candidate_to_row(row, gap, candidate))
        updated, accepted = apply_candidates_to_row(row, candidates, gap)
        updated_rows.append(updated)
        candidate_rows.extend(accepted)
    return updated_rows, candidate_rows


def web_search_updates(
    profile,
    rows: list[dict[str, str]],
    gap: str,
    mode: str,
    max_queries: int | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if mode == "off":
        return rows, []
    session = requests.Session()
    updated_rows: list[dict[str, str]] = []
    candidate_rows: list[dict[str, str]] = []
    for row in rows:
        if not is_missing_gap(row, gap):
            updated_rows.append(row)
            continue
        candidates = web_search_candidates(profile, row, gap, session=session, max_queries=max_queries)
        for candidate in candidates:
            candidate_rows.append(candidate_to_row(row, gap, candidate))
        updated, accepted = apply_candidates_to_row(row, candidates, gap)
        updated_rows.append(updated)
        candidate_rows.extend(accepted)
        time.sleep(0.25)
    return updated_rows, candidate_rows


def build_gap_firecrawl_queue(profile, rows: list[dict[str, str]], gap: str, input_fields: list[str]) -> list[dict[str, str]]:
    queue: list[dict[str, str]] = []
    for row in rows:
        queued = dict(row)
        queued["gap_fill_requested"] = gap
        queued["gap_queue_status"] = "queued"
        queued["gap_queue_reason"] = f"{gap} still missing after existing/raw/web-search stages"
        queued["gap_skip_reason"] = ""
        if not is_missing_gap(row, gap):
            queued["gap_queue_status"] = "skipped"
            queued["gap_skip_reason"] = "gap_already_filled"
        elif not clean(row.get("website")) or not root_domain_for_row(row):
            queued["gap_queue_status"] = "skipped"
            queued["gap_skip_reason"] = "no_usable_website"
        elif row.get("enriched_fit_status") and row.get("enriched_fit_status") != "campaign_target":
            queued["gap_queue_status"] = "skipped"
            queued["gap_skip_reason"] = "not_campaign_target"
        queue.append(queued)
    return queue


def completed_gap_keys(path: Path) -> set[tuple[str, str]]:
    rows = read_csv(path)
    return {identity_key(row) for row in rows}


def gap_firecrawl_config(profile) -> dict[str, object]:
    config = dict(getattr(profile, "GAP_FILL_FIRECRAWL", {}) or {})
    if not config:
        config = dict(getattr(profile, "FIRECRAWL", {}) or {})
    if "schema" not in config and hasattr(profile, "GAP_FILL_FIRECRAWL_SCHEMA"):
        config["schema"] = getattr(profile, "GAP_FILL_FIRECRAWL_SCHEMA")
    if "prompt" not in config and hasattr(profile, "GAP_FILL_FIRECRAWL_PROMPT"):
        config["prompt"] = getattr(profile, "GAP_FILL_FIRECRAWL_PROMPT")
    if "paths" not in config:
        config["paths"] = target_paths(profile)
    return config


def people_url_keywords(profile) -> dict[str, int]:
    configured = getattr(profile, "GAP_FILL_PEOPLE_URL_KEYWORDS", None)
    if isinstance(configured, dict) and configured:
        return dict(configured)
    return {
        "team": 10,
        "staff": 10,
        "leadership": 10,
        "people": 9,
        "agents": 9,
        "brokers": 9,
        "realtors": 8,
        "about": 7,
        "about-us": 7,
        "contact": 6,
        "owner": 6,
        "management": 4,
    }


def negative_people_url_keywords(profile) -> set[str]:
    configured = getattr(profile, "GAP_FILL_PEOPLE_URL_NEGATIVE_KEYWORDS", None)
    if configured:
        return {str(item).lower() for item in configured}
    return {
        "privacy",
        "terms",
        "login",
        "portal",
        "resident",
        "tenant",
        "apply",
        "application",
        "vacanc",
        "available",
        "listing",
        "blog",
        "careers",
        "jobs",
        "maintenance",
        "payment",
        "sitemap",
        ".xml",
    }


def root_for_url(url: str) -> str:
    return normalize_domain(url)


def url_blob(url: str, title: str = "", description: str = "") -> str:
    parsed = urlparse(url)
    return clean(f"{parsed.path} {parsed.query} {title} {description}").lower()


def score_people_url(profile, row: dict[str, str], url: str, title: str = "", description: str = "") -> int:
    if hasattr(profile, "gap_fill_score_url"):
        return int(profile.gap_fill_score_url(row, url, title, description))
    normalized = normalize_url(url)
    if not normalized:
        return -999
    target_root = root_domain_for_row(row)
    result_root = root_for_url(normalized)
    if target_root and result_root and target_root != result_root:
        # External search results can still be useful, but keep them behind site-owned people pages.
        score = 1
    else:
        score = 5
    blob = url_blob(normalized, title, description)
    if any(term in blob for term in negative_people_url_keywords(profile)):
        score -= 12
    for keyword, weight in people_url_keywords(profile).items():
        if keyword.lower() in blob:
            score += int(weight)
    if "linkedin.com" in normalized.lower():
        score += 4
    return score


def search_result_values(result: object) -> tuple[str, str, str]:
    if isinstance(result, dict):
        return (
            clean(result.get("url")),
            clean(result.get("title")),
            clean(result.get("description") or result.get("snippet")),
        )
    return (
        clean(getattr(result, "url", "")),
        clean(getattr(result, "title", "")),
        clean(getattr(result, "description", "")),
    )


def discover_people_urls(
    client,
    profile,
    row: dict[str, str],
    map_limit: int = 60,
    search_limit: int = 4,
    max_urls: int = 5,
) -> tuple[list[str], list[str], int, str]:
    base_url = normalize_url(row.get("website") or "")
    if not base_url:
        return [], [], 0, "invalid_website"
    discovered: list[tuple[int, str, str]] = []
    discovery_sources: list[str] = []
    credits = 0
    error = ""

    try:
        mapped = client.map(
            base_url,
            limit=map_limit,
            ignore_query_parameters=True,
            sitemap="include",
            timeout=60000,
        )
        credits += response_credits(mapped)
        for link in getattr(mapped, "links", []) or []:
            url, title, description = search_result_values(link)
            if not url:
                continue
            score = score_people_url(profile, row, url, title, description)
            target_root = root_domain_for_row(row)
            result_root = root_for_url(url)
            if target_root and result_root != target_root:
                discovery_sources.append(f"map_external_skipped:{url}")
                continue
            if score > 0:
                discovered.append((score, normalize_url(url), "map"))
                discovery_sources.append(f"map:{url}")
    except Exception as exc:
        error = f"map_error: {exc}"

    for query in default_search_queries(profile, row)[:search_limit]:
        try:
            results = client.search(
                query,
                sources=["web"],
                limit=5,
                ignore_invalid_urls=True,
                timeout=60000,
            )
            credits += response_credits(results)
        except Exception as exc:
            if error:
                error = f"{error} | search_error: {exc}"
            else:
                error = f"search_error: {exc}"
            continue
        for result in getattr(results, "web", []) or []:
            url, title, description = search_result_values(result)
            if not url:
                continue
            if not result_matches_row(row, {"title": title, "snippet": description, "url": url}):
                # Allow LinkedIn or same-domain results through even when snippets are thin.
                target_root = root_domain_for_row(row)
                result_root = root_for_url(url)
                if "linkedin.com" not in url.lower() and target_root != result_root:
                    continue
            target_root = root_domain_for_row(row)
            result_root = root_for_url(url)
            if target_root and result_root != target_root:
                discovery_sources.append(f"search_external_reference:{query}:{url}")
                continue
            score = score_people_url(profile, row, url, title, description)
            if score > 0:
                discovered.append((score, normalize_url(url), "search"))
                discovery_sources.append(f"search:{query}:{url}")

    if not discovered:
        return [base_url], unique_strings(discovery_sources), credits, error
    deduped: dict[str, tuple[int, str]] = {}
    for score, url, source in discovered:
        if not url:
            continue
        existing = deduped.get(url)
        if existing is None or score > existing[0]:
            deduped[url] = (score, source)
    ranked = sorted(deduped.items(), key=lambda item: (-item[1][0], len(item[0]), item[0]))
    selected = [url for url, _meta in ranked[:max_urls]]
    if base_url not in selected and len(selected) < max_urls:
        selected.append(base_url)
    return selected, unique_strings(discovery_sources), credits, error


def apply_firecrawl_gap_result(profile, row: dict[str, str], page_results: list[dict[str, object]], gap: str) -> dict[str, str]:
    if hasattr(profile, "apply_gap_fill_enrichment"):
        return dict(profile.apply_gap_fill_enrichment(row, page_results, gap))
    candidates = candidates_from_firecrawl_json(profile, {**row, "firecrawl_json": json.dumps(page_results)}, gap)
    updated, _accepted = apply_candidates_to_row(row, candidates, gap)
    return {
        key: value
        for key, value in updated.items()
        if key in GAP_EXTRA_FIELDS or key in {"decision_makers", "email"}
    }


def run_paid_gap_firecrawl(
    profile,
    queue_rows: list[dict[str, str]],
    enriched_path: Path,
    failed_path: Path,
    fields: list[str],
    gap: str,
    max_rows: int | None = None,
    max_credits: int | None = None,
    resume: bool = True,
) -> dict[str, object]:
    ensure_csv_header(enriched_path, fields)
    ensure_csv_header(failed_path, fields)
    summary = {
        "gap_firecrawl_mode": "paid",
        "gap_firecrawl_queue_rows": len(queue_rows),
        "gap_firecrawl_queued": sum(1 for row in queue_rows if row.get("gap_queue_status") == "queued"),
        "gap_firecrawl_skipped": sum(1 for row in queue_rows if row.get("gap_queue_status") != "queued"),
        "gap_firecrawl_attempted": 0,
        "gap_firecrawl_enriched": 0,
        "gap_firecrawl_failed": 0,
        "gap_firecrawl_credits_used": 0,
        "gap_firecrawl_stopped_reason": None,
    }
    if FirecrawlApp is None:
        summary["gap_firecrawl_stopped_reason"] = "missing_firecrawl_package"
        return summary
    api_key = firecrawl_api_key()
    if not api_key:
        summary["gap_firecrawl_stopped_reason"] = "missing_firecrawl_api_key"
        return summary
    config = gap_firecrawl_config(profile)
    schema = config.get("schema")
    prompt = config.get("prompt")
    paths = list(config.get("paths") or target_paths(profile))
    max_pages = int(config.get("max_pages") or len(paths))
    estimate = int(config.get("credit_estimate_per_page") or 5)
    if not schema or not prompt:
        summary["gap_firecrawl_stopped_reason"] = "missing_profile_gap_firecrawl_schema"
        return summary

    done = set()
    if resume:
        done = completed_gap_keys(enriched_path) | completed_gap_keys(failed_path)
    candidates = [
        row for row in queue_rows
        if row.get("gap_queue_status") == "queued" and (not resume or identity_key(row) not in done)
    ]
    if max_rows is not None:
        candidates = candidates[:max_rows]
    client = FirecrawlApp(api_key=api_key)
    for row in candidates:
        base_url = normalize_url(row.get("website") or "")
        if not base_url:
            failed = {**row, "gap_firecrawl_status": "failed", "gap_firecrawl_error": "invalid_website"}
            append_csv_row(failed_path, failed, fields)
            summary["gap_firecrawl_failed"] = int(summary["gap_firecrawl_failed"]) + 1
            continue
        summary["gap_firecrawl_attempted"] = int(summary["gap_firecrawl_attempted"]) + 1
        page_results: list[dict[str, object]] = []
        credits_used = 0
        pages_attempted = 0
        source_pages: list[str] = []
        error = ""
        for path in paths[:max_pages]:
            if max_credits is not None and int(summary["gap_firecrawl_credits_used"]) + estimate > max_credits:
                summary["gap_firecrawl_stopped_reason"] = "max_credits"
                break
            url = url_for_path(base_url, path)
            pages_attempted += 1
            data, credits, status = scrape_with_retries(client, url, schema, prompt)
            credits_used += credits
            summary["gap_firecrawl_credits_used"] = int(summary["gap_firecrawl_credits_used"]) + credits
            source_pages.append(url)
            if status:
                error = status
                if status == "skipped_no_credits":
                    summary["gap_firecrawl_stopped_reason"] = "skipped_no_credits"
                    break
                if status in {"skipped_rate_limit"} or status.startswith("skipped_api_error"):
                    break
                continue
            if data:
                page_results.append({"url": url, "data": data})
            if page_results and not is_missing_gap(apply_firecrawl_gap_result(profile, row, page_results, gap), gap):
                break
        if error == "skipped_no_credits" and not page_results:
            summary["gap_firecrawl_stopped_reason"] = "skipped_no_credits"
            break
        result = {
            **row,
            "gap_firecrawl_credits_used": str(credits_used),
            "gap_firecrawl_pages_attempted": str(pages_attempted),
            "gap_firecrawl_source_pages": " | ".join(source_pages),
            "gap_firecrawl_error": error,
            "gap_firecrawl_json": json.dumps(page_results, ensure_ascii=True),
        }
        if page_results:
            result["gap_firecrawl_status"] = "enriched"
            enrichment = apply_firecrawl_gap_result(profile, row, page_results, gap)
            protected = {
                "gap_firecrawl_status",
                "gap_firecrawl_credits_used",
                "gap_firecrawl_pages_attempted",
                "gap_firecrawl_source_pages",
                "gap_firecrawl_error",
                "gap_firecrawl_json",
            }
            for key, value in enrichment.items():
                if key in protected:
                    continue
                if clean(value):
                    result[key] = value
            append_csv_row(enriched_path, result, fields)
            summary["gap_firecrawl_enriched"] = int(summary["gap_firecrawl_enriched"]) + 1
        else:
            result["gap_firecrawl_status"] = "failed"
            result["gap_firecrawl_error"] = error or "no_structured_data"
            append_csv_row(failed_path, result, fields)
            summary["gap_firecrawl_failed"] = int(summary["gap_firecrawl_failed"]) + 1
        if summary.get("gap_firecrawl_stopped_reason") in {"max_credits", "skipped_no_credits"}:
            break
    return summary


def run_paid_gap_firecrawl_map_search(
    profile,
    queue_rows: list[dict[str, str]],
    enriched_path: Path,
    failed_path: Path,
    fields: list[str],
    gap: str,
    max_rows: int | None = None,
    max_credits: int | None = None,
    resume: bool = True,
    map_limit: int = 60,
    search_limit: int = 4,
    max_discovered_urls: int = 5,
) -> dict[str, object]:
    ensure_csv_header(enriched_path, fields)
    ensure_csv_header(failed_path, fields)
    summary = {
        "gap_firecrawl_mode": "paid",
        "gap_firecrawl_strategy": "map-search",
        "gap_firecrawl_queue_rows": len(queue_rows),
        "gap_firecrawl_queued": sum(1 for row in queue_rows if row.get("gap_queue_status") == "queued"),
        "gap_firecrawl_skipped": sum(1 for row in queue_rows if row.get("gap_queue_status") != "queued"),
        "gap_firecrawl_attempted": 0,
        "gap_firecrawl_enriched": 0,
        "gap_firecrawl_failed": 0,
        "gap_firecrawl_credits_used": 0,
        "gap_firecrawl_stopped_reason": None,
    }
    if FirecrawlApp is None:
        summary["gap_firecrawl_stopped_reason"] = "missing_firecrawl_package"
        return summary
    api_key = firecrawl_api_key()
    if not api_key:
        summary["gap_firecrawl_stopped_reason"] = "missing_firecrawl_api_key"
        return summary
    config = gap_firecrawl_config(profile)
    schema = config.get("schema")
    prompt = config.get("prompt")
    estimate = int(config.get("credit_estimate_per_page") or 5)
    if not schema or not prompt:
        summary["gap_firecrawl_stopped_reason"] = "missing_profile_gap_firecrawl_schema"
        return summary

    done = set()
    if resume:
        done = completed_gap_keys(enriched_path) | completed_gap_keys(failed_path)
    candidates = [
        row for row in queue_rows
        if row.get("gap_queue_status") == "queued" and (not resume or identity_key(row) not in done)
    ]
    if max_rows is not None:
        candidates = candidates[:max_rows]

    client = FirecrawlApp(api_key=api_key)
    for row in candidates:
        base_url = normalize_url(row.get("website") or "")
        if not base_url:
            failed = {
                **row,
                "gap_firecrawl_strategy": "map-search",
                "gap_firecrawl_status": "failed",
                "gap_firecrawl_error": "invalid_website",
            }
            append_csv_row(failed_path, failed, fields)
            summary["gap_firecrawl_failed"] = int(summary["gap_firecrawl_failed"]) + 1
            continue

        summary["gap_firecrawl_attempted"] = int(summary["gap_firecrawl_attempted"]) + 1
        selected_urls, discovery_sources, discovery_credits, discovery_error = discover_people_urls(
            client,
            profile,
            row,
            map_limit=map_limit,
            search_limit=search_limit,
            max_urls=max_discovered_urls,
        )
        credits_used = discovery_credits
        summary["gap_firecrawl_credits_used"] = int(summary["gap_firecrawl_credits_used"]) + discovery_credits
        page_results: list[dict[str, object]] = []
        pages_attempted = 0
        source_pages: list[str] = []
        error = discovery_error

        for url in selected_urls:
            if max_credits is not None and int(summary["gap_firecrawl_credits_used"]) + estimate > max_credits:
                summary["gap_firecrawl_stopped_reason"] = "max_credits"
                break
            pages_attempted += 1
            data, credits, status = scrape_with_retries(client, url, schema, prompt)
            credits_used += credits
            summary["gap_firecrawl_credits_used"] = int(summary["gap_firecrawl_credits_used"]) + credits
            source_pages.append(url)
            if status:
                error = append_unique(error, [status])
                if status == "skipped_no_credits":
                    summary["gap_firecrawl_stopped_reason"] = "skipped_no_credits"
                    break
                if status in {"skipped_rate_limit"} or status.startswith("skipped_api_error"):
                    break
                continue
            if data:
                page_results.append({"url": url, "data": data})
                merged_update = apply_firecrawl_gap_result(profile, row, page_results, gap)
                if not is_missing_gap({**row, **merged_update}, gap):
                    break

        if error and "skipped_no_credits" in error and not page_results:
            summary["gap_firecrawl_stopped_reason"] = "skipped_no_credits"
            break
        result = {
            **row,
            "gap_firecrawl_strategy": "map-search",
            "gap_firecrawl_credits_used": str(credits_used),
            "gap_firecrawl_pages_attempted": str(pages_attempted),
            "gap_firecrawl_source_pages": " | ".join(source_pages),
            "gap_discovery_source_urls": " | ".join(discovery_sources),
            "gap_firecrawl_error": error,
            "gap_firecrawl_json": json.dumps(page_results, ensure_ascii=True),
        }
        if page_results:
            result["gap_firecrawl_status"] = "enriched"
            enrichment = apply_firecrawl_gap_result(profile, row, page_results, gap)
            protected = {
                "gap_firecrawl_strategy",
                "gap_firecrawl_status",
                "gap_firecrawl_credits_used",
                "gap_firecrawl_pages_attempted",
                "gap_firecrawl_source_pages",
                "gap_firecrawl_error",
                "gap_firecrawl_json",
                "gap_discovery_source_urls",
            }
            for key, value in enrichment.items():
                if key in protected:
                    continue
                if clean(value):
                    result[key] = value
            append_csv_row(enriched_path, result, fields)
            summary["gap_firecrawl_enriched"] = int(summary["gap_firecrawl_enriched"]) + 1
        else:
            result["gap_firecrawl_status"] = "failed"
            result["gap_firecrawl_error"] = error or "no_structured_data"
            append_csv_row(failed_path, result, fields)
            summary["gap_firecrawl_failed"] = int(summary["gap_firecrawl_failed"]) + 1

        if summary.get("gap_firecrawl_stopped_reason") in {"max_credits", "skipped_no_credits"}:
            break
    return summary


def merge_gap_firecrawl_rows(rows: list[dict[str, str]], enriched_rows: list[dict[str, str]], fields: list[str], gap: str) -> list[dict[str, str]]:
    updates = {identity_key(row): row for row in enriched_rows}
    merged: list[dict[str, str]] = []
    for row in rows:
        update = updates.get(identity_key(row))
        if not update:
            merged.append(row)
            continue
        combined = dict(row)
        for field in fields:
            value = clean(update.get(field))
            if value and (field in GAP_EXTRA_FIELDS or not clean(combined.get(field))):
                combined[field] = update.get(field, "")
        combined["gap_fill_requested"] = gap
        if not is_missing_gap(combined, gap):
            combined["gap_fill_status"] = "filled"
            combined["gap_fill_sources"] = append_unique(combined.get("gap_fill_sources"), ["firecrawl_gap_fill"])
            if gap == "decision_makers":
                combined["gap_filled_fields"] = append_unique(combined.get("gap_filled_fields"), ["decision_makers"])
            elif gap == "emails":
                combined["gap_filled_fields"] = append_unique(combined.get("gap_filled_fields"), ["email"])
        merged.append(combined)
    return merged


def write_gap_qa(path: Path, profile, gap: str, summary: dict[str, object]) -> None:
    lines = [
        f"# {getattr(profile, 'DISPLAY_NAME', getattr(profile, 'NICHE_ID', 'Niche'))} Gap Fill QA",
        "",
        f"- Niche: {getattr(profile, 'NICHE_ID', '')}",
        f"- Gap: {gap}",
        f"- Input rows: {summary.get('input_rows', 0)}",
        f"- Missing decision makers before: {summary.get('missing_decision_makers_before', 0)}",
        f"- Missing emails before: {summary.get('missing_emails_before', 0)}",
        f"- Existing-data updates: {summary.get('existing_data_updates', 0)}",
        f"- Raw website candidates: {summary.get('raw_website_candidates', 0)}",
        f"- Web search candidates: {summary.get('web_search_candidates', 0)}",
        f"- Firecrawl mode: {summary.get('firecrawl_mode', 'off')}",
        f"- Firecrawl queued: {summary.get('gap_firecrawl_queued', 0)}",
        f"- Firecrawl skipped: {summary.get('gap_firecrawl_skipped', 0)}",
        f"- Firecrawl attempted: {summary.get('gap_firecrawl_attempted', 0)}",
        f"- Firecrawl enriched: {summary.get('gap_firecrawl_enriched', 0)}",
        f"- Firecrawl failed: {summary.get('gap_firecrawl_failed', 0)}",
        f"- Firecrawl credits used: {summary.get('gap_firecrawl_credits_used', 0)}",
        f"- Firecrawl stopped reason: {summary.get('gap_firecrawl_stopped_reason')}",
        f"- Missing decision makers after: {summary.get('missing_decision_makers_after', 0)}",
        f"- Missing emails after: {summary.get('missing_emails_after', 0)}",
        f"- Final rows: {summary.get('final_rows', 0)}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
