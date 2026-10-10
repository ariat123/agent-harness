"""Bounded, source-neutral website fetching and evidence extraction."""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import asdict, dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from enrichment_utils import (
    EMAIL_RE,
    PHONE_RE,
    email_context,
    is_blocked_email,
    is_non_site_url,
    norm_email,
    normalize_url,
)
from lead_scraper_core.normalize import clean, root_domain


MAX_RESPONSE_BYTES = 1_000_000
MAX_REDIRECTS = 5
THIN_TEXT_CHARACTERS = 200
MAX_SERVICE_SIGNALS = 12
MAX_SERVICE_SIGNAL_CANDIDATES = 60
MAX_SERVICE_SIGNAL_CHARACTERS = 200
USER_AGENT = "Mozilla/5.0 (compatible; CompanyDiscoveryBot/2.0; bounded research)"

LINK_KINDS = {
    "contact": 0,
    "about": 1,
    "team": 2,
    "leadership": 2,
    "services": 3,
    "service": 3,
    "inspections": 3,
    "inspection": 3,
    "locations": 4,
    "location": 4,
    "careers": 5,
    "career": 5,
}
ANTI_BOT_TERMS = (
    "access denied",
    "checking your browser",
    "enable javascript",
    "captcha",
    "cloudflare ray id",
    "verify you are human",
)


@dataclass(frozen=True)
class HttpPageResult:
    requested_url: str
    final_url: str
    success: bool
    status_code: int | None
    content: str
    content_type: str
    bytes_read: int
    redirects: tuple[dict[str, object], ...]
    error: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["redirects"] = list(self.redirects)
        return data


@dataclass(frozen=True)
class FirecrawlDocument:
    requested_url: str
    final_url: str
    source_url: str
    html: str
    markdown: str
    metadata: dict[str, object]


def homepage_url(website: str) -> str:
    raw = clean(website)
    if "://" in raw and urlparse(raw).scheme.lower() not in {"http", "https"}:
        return ""
    normalized = normalize_url(website)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def _public_addresses(host: str, resolver: Callable[..., object]) -> list[str]:
    try:
        return [str(info[4][0]) for info in resolver(host, None, type=socket.SOCK_STREAM)]
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"hostname_resolution_failed:{exc.__class__.__name__}") from exc


def validate_public_url(
    url: str,
    expected_root_domain: str = "",
    *,
    resolver: Callable[..., object] = socket.getaddrinfo,
) -> str:
    raw = clean(url)
    if "://" in raw and urlparse(raw).scheme.lower() not in {"http", "https"}:
        raise ValueError("unsafe_scheme")
    normalized = normalize_url(url)
    if not normalized:
        raise ValueError("malformed_url")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("unsafe_scheme")
    if parsed.username or parsed.password:
        raise ValueError("url_credentials_not_allowed")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("unsafe_hostname")
    if is_non_site_url(normalized):
        raise ValueError("non_company_website")
    if expected_root_domain and root_domain(normalized) != expected_root_domain.lower():
        raise ValueError("cross_domain_url")

    try:
        addresses = [host] if ipaddress.ip_address(host) else []
    except ValueError:
        addresses = _public_addresses(host, resolver)
    if not addresses:
        raise ValueError("hostname_has_no_addresses")
    for address in addresses:
        try:
            parsed_ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise ValueError("hostname_returned_invalid_address") from exc
        if not parsed_ip.is_global:
            raise ValueError("private_or_nonpublic_address")
    return normalized


def _response_body(response, max_response_bytes: int) -> tuple[bytes, str]:
    length = response.headers.get("Content-Length") or response.headers.get("content-length")
    if length:
        try:
            if int(length) > max_response_bytes:
                return b"", "response_too_large"
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65_536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_response_bytes:
            return b"", "response_too_large"
        chunks.append(chunk)
    return b"".join(chunks), ""


def fetch_http_page(
    url: str,
    *,
    timeout_seconds: int,
    expected_root_domain: str,
    session: requests.Session | None = None,
    resolver: Callable[..., object] = socket.getaddrinfo,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> HttpPageResult:
    session = session or requests.Session()
    current = url
    redirects: list[dict[str, object]] = []
    response = None
    for _ in range(MAX_REDIRECTS + 1):
        try:
            safe_url = validate_public_url(current, expected_root_domain, resolver=resolver)
        except ValueError as exc:
            return HttpPageResult(url, current, False, None, "", "", 0, tuple(redirects), str(exc))
        try:
            response = session.get(
                safe_url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                timeout=timeout_seconds,
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout:
            return HttpPageResult(url, safe_url, False, None, "", "", 0, tuple(redirects), "timeout")
        except requests.RequestException as exc:
            return HttpPageResult(
                url,
                safe_url,
                False,
                None,
                "",
                "",
                0,
                tuple(redirects),
                f"network_error:{exc.__class__.__name__}",
            )

        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location") or response.headers.get("location")
            if not location:
                return HttpPageResult(
                    url,
                    safe_url,
                    False,
                    response.status_code,
                    "",
                    "",
                    0,
                    tuple(redirects),
                    "redirect_without_location",
                )
            next_url = urljoin(safe_url, location)
            redirects.append({"status_code": response.status_code, "from": safe_url, "to": next_url})
            response.close()
            current = next_url
            continue

        content_type = clean(response.headers.get("Content-Type") or response.headers.get("content-type")).lower()
        try:
            body, body_error = _response_body(response, max_response_bytes)
        except requests.RequestException as exc:
            response.close()
            return HttpPageResult(
                url,
                safe_url,
                False,
                response.status_code,
                "",
                content_type,
                0,
                tuple(redirects),
                f"network_error:{exc.__class__.__name__}",
            )
        response.close()
        if body_error:
            return HttpPageResult(
                url,
                safe_url,
                False,
                response.status_code,
                "",
                content_type,
                0,
                tuple(redirects),
                body_error,
            )
        encoding = getattr(response, "encoding", None) or "utf-8"
        content = body.decode(encoding, errors="replace")
        if response.status_code < 200 or response.status_code >= 300:
            return HttpPageResult(
                url,
                safe_url,
                False,
                response.status_code,
                content,
                content_type,
                len(body),
                tuple(redirects),
                f"http_{response.status_code}",
            )
        if content_type and not any(kind in content_type for kind in ("text/html", "application/xhtml", "text/plain")):
            return HttpPageResult(
                url,
                safe_url,
                False,
                response.status_code,
                content,
                content_type,
                len(body),
                tuple(redirects),
                "unsupported_content_type",
            )
        return HttpPageResult(
            url,
            safe_url,
            True,
            response.status_code,
            content,
            content_type,
            len(body),
            tuple(redirects),
            "",
        )
    return HttpPageResult(url, current, False, None, "", "", 0, tuple(redirects), "too_many_redirects")


def _link_kind(value: str) -> tuple[int, str] | None:
    lowered = value.lower()
    matches = [(priority, kind) for kind, priority in LINK_KINDS.items() if re.search(rf"\b{re.escape(kind)}\b", lowered)]
    if not matches:
        return None
    priority, kind = min(matches)
    return priority, {"career": "careers", "location": "locations"}.get(kind, kind)


def _same_domain_link_candidate(
    href: str,
    anchor_text: str,
    base_url: str,
    expected_root_domain: str,
) -> dict[str, object] | None:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    normalized = normalize_url(urljoin(base_url, href))
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None
    if root_domain(normalized) != expected_root_domain.lower() or is_non_site_url(normalized):
        return None
    kind_match = _link_kind(parsed.path) or _link_kind(anchor_text)
    if not kind_match:
        return None
    priority, kind = kind_match
    return {
        "url": normalized,
        "kind": kind,
        "priority": priority,
        "anchor_text": _markdown_inline_text(anchor_text),
    }


def prioritized_same_domain_links(markup: str, base_url: str, expected_root_domain: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(markup or "", "html.parser")
    candidates: dict[str, dict[str, object]] = {}
    for anchor in soup.select("a[href]"):
        href = clean(anchor.get("href"))
        candidate = _same_domain_link_candidate(
            href,
            anchor.get_text(" ", strip=True),
            base_url,
            expected_root_domain,
        )
        if not candidate:
            continue
        normalized = str(candidate["url"])
        priority = int(candidate["priority"])
        current = candidates.get(normalized)
        if current is None or priority < int(current["priority"]):
            candidates[normalized] = candidate
    return sorted(candidates.values(), key=lambda item: (int(item["priority"]), str(item["url"])))


MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]{0,240})\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")


def prioritized_same_domain_markdown_links(
    markdown: str,
    base_url: str,
    expected_root_domain: str,
) -> list[dict[str, object]]:
    candidates: dict[str, dict[str, object]] = {}
    for match in MARKDOWN_LINK_RE.finditer(markdown or ""):
        candidate = _same_domain_link_candidate(
            clean(match.group(2)),
            _markdown_inline_text(match.group(1)),
            base_url,
            expected_root_domain,
        )
        if not candidate:
            continue
        normalized = str(candidate["url"])
        current = candidates.get(normalized)
        if current is None or int(candidate["priority"]) < int(current["priority"]):
            candidates[normalized] = candidate
    return sorted(candidates.values(), key=lambda item: (int(item["priority"]), str(item["url"])))


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup.select("script,style,noscript,svg"):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _concise_signal(value: object) -> str:
    signal = clean(value)
    if not 5 <= len(signal) <= MAX_SERVICE_SIGNAL_CHARACTERS:
        return ""
    if signal.lower().startswith(("http://", "https://")):
        return ""
    if not re.search(r"[A-Za-z0-9]", signal) or re.search(r"(?:\b[A-Za-z]\s+){5,}", signal):
        return ""
    return signal


def _unique_signals(
    values: list[object],
    source_url: str,
    *,
    limit: int = MAX_SERVICE_SIGNALS,
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        signal = _concise_signal(value)
        key = signal.casefold()
        if not signal or key in seen:
            continue
        signals.append({"signal": signal, "source_url": source_url})
        seen.add(key)
        if len(signals) >= limit:
            break
    return signals


def _service_signal_priority(value: object) -> int:
    text = clean(value).lower()
    return 0 if re.search(
        r"\b(service|services|inspection|inspections|installation|maintenance|repair|testing|monitoring|system|systems|solution|solutions)\b",
        text,
    ) else 1


def _html_meta_description(soup: BeautifulSoup) -> str:
    for selector in (
        'meta[name="description"]',
        'meta[property="og:description"]',
        'meta[name="og:description"]',
    ):
        tag = soup.select_one(selector)
        value = clean(tag.get("content")) if tag else ""
        if value:
            return value
    return ""


def _service_list_items(soup: BeautifulSoup, page_kind: str) -> list[str]:
    service_page = page_kind in {"service", "services", "inspection", "inspections"}
    items: list[str] = []
    for item in soup.select("li"):
        context_parts: list[str] = []
        for parent in item.parents:
            if getattr(parent, "name", "") in {"body", "html"}:
                break
            context_parts.extend(
                [
                    clean(parent.get("id")),
                    " ".join(str(value) for value in (parent.get("class") or [])),
                ]
            )
        context = " ".join(context_parts).lower()
        if not service_page and not re.search(r"\b(service|services|solution|solutions|capabilit|expertise|inspection)", context):
            continue
        value = clean(item.get_text(" ", strip=True))
        if value:
            items.append(value)
    return items


def extract_page_evidence(markup: str, source_url: str, *, source: str, page_kind: str = "") -> dict[str, object]:
    soup = BeautifulSoup(markup or "", "html.parser")
    page_title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    meta_description = _html_meta_description(soup)
    headings = [clean(tag.get_text(" ", strip=True)) for tag in soup.select("h1,h2,h3")]
    service_items = _service_list_items(soup, page_kind)
    visible_text = _visible_text(soup)
    emails: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href^='mailto:']"):
        email = norm_email(anchor.get("href"))
        if email and not is_blocked_email(email, email_context(visible_text, email)):
            emails[email] = {"email": email, "source_url": source_url, "source": source}
    for raw in EMAIL_RE.findall(visible_text):
        email = norm_email(raw)
        if email and not is_blocked_email(email, email_context(visible_text, email)):
            emails[email] = {"email": email, "source_url": source_url, "source": source}

    phones: dict[str, dict[str, str]] = {}
    for raw in PHONE_RE.findall(visible_text):
        phone = clean(raw)
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 10:
            phones[digits[-10:]] = {"phone": phone, "source_url": source_url, "source": source}

    lowered = visible_text.lower()
    return {
        "visible_text_characters": len(visible_text),
        "thin": len(visible_text) < THIN_TEXT_CHARACTERS,
        "anti_bot": any(term in lowered for term in ANTI_BOT_TERMS),
        "page_title": page_title,
        "meta_description": meta_description,
        "email_candidates": list(emails.values()),
        "phone_candidates": list(phones.values()),
        "service_signals": _unique_signals(
            [page_title, meta_description, *headings, *service_items],
            source_url,
            limit=MAX_SERVICE_SIGNAL_CANDIDATES,
        ),
    }


def _markdown_inline_text(value: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value or "")
    text = MARKDOWN_LINK_RE.sub(lambda match: match.group(1), text)
    text = re.sub(r"[`*_~]", "", text)
    return clean(text)


def _markdown_visible_text(markdown: str) -> str:
    lines = []
    in_fence = False
    for raw_line in (markdown or "").splitlines():
        if raw_line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = re.sub(r"^\s{0,3}(?:#{1,6}|[-*+]|\d+[.)])\s+", "", raw_line)
        lines.append(_markdown_inline_text(line))
    return clean(" ".join(lines))


def extract_markdown_evidence(
    markdown: str,
    source_url: str,
    *,
    source: str,
    page_kind: str = "",
) -> dict[str, object]:
    visible_text = _markdown_visible_text(markdown)
    emails: dict[str, dict[str, str]] = {}
    phones: dict[str, dict[str, str]] = {}
    for match in MARKDOWN_LINK_RE.finditer(markdown or ""):
        href = clean(match.group(2))
        if href.lower().startswith("mailto:"):
            email = norm_email(href)
            if email and not is_blocked_email(email, email_context(visible_text, email)):
                emails[email] = {"email": email, "source_url": source_url, "source": source}
        elif href.lower().startswith("tel:"):
            phone = clean(href.split(":", 1)[1])
            digits = re.sub(r"\D", "", phone)
            if len(digits) >= 10:
                phones[digits[-10:]] = {"phone": phone, "source_url": source_url, "source": source}
    for raw in EMAIL_RE.findall(visible_text):
        email = norm_email(raw)
        if email and not is_blocked_email(email, email_context(visible_text, email)):
            emails[email] = {"email": email, "source_url": source_url, "source": source}
    for raw in PHONE_RE.findall(visible_text):
        phone = clean(raw)
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 10:
            phones[digits[-10:]] = {"phone": phone, "source_url": source_url, "source": source}

    headings: list[str] = []
    title = ""
    current_heading = ""
    service_items: list[str] = []
    service_page = page_kind in {"service", "services", "inspection", "inspections"}
    for raw_line in (markdown or "").splitlines():
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", raw_line)
        if heading:
            current_heading = _markdown_inline_text(heading.group(2))
            headings.append(current_heading)
            if not title and len(heading.group(1)) == 1:
                title = current_heading
            continue
        item = re.match(r"^\s{0,3}[-*+]\s+(.+)$", raw_line)
        if item and (
            service_page
            or re.search(r"\b(service|services|solution|solutions|capabilit|expertise|inspection)", current_heading.lower())
        ):
            service_items.append(_markdown_inline_text(item.group(1)))

    lowered = visible_text.lower()
    return {
        "visible_text_characters": len(visible_text),
        "thin": len(visible_text) < THIN_TEXT_CHARACTERS,
        "anti_bot": any(term in lowered for term in ANTI_BOT_TERMS),
        "page_title": title,
        "meta_description": "",
        "email_candidates": list(emails.values()),
        "phone_candidates": list(phones.values()),
        "service_signals": _unique_signals(
            [title, *headings, *service_items],
            source_url,
            limit=MAX_SERVICE_SIGNAL_CANDIDATES,
        ),
    }


def serialize_firecrawl_result(result: object) -> dict[str, object]:
    if isinstance(result, dict):
        return result
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        data = model_dump(exclude_none=True)
        return data if isinstance(data, dict) else {"value": data}
    data: dict[str, object] = {}
    for name in ("markdown", "html", "raw_html", "json", "summary", "metadata", "links", "warning"):
        value = getattr(result, name, None)
        if value is None:
            continue
        nested_dump = getattr(value, "model_dump", None)
        data[name] = nested_dump(exclude_none=True) if callable(nested_dump) else value
    return data


def _provider_document(data: dict[str, object]) -> dict[str, object]:
    for key in ("data", "document"):
        nested = data.get(key)
        if isinstance(nested, dict) and any(
            name in nested for name in ("html", "raw_html", "rawHtml", "markdown", "metadata")
        ):
            return nested
    return data


def _safe_provider_url(value: object, expected_root_domain: str) -> str:
    raw = clean(value)
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    normalized = normalize_url(raw) or ""
    if not normalized or is_non_site_url(normalized):
        return ""
    if expected_root_domain and root_domain(normalized) != expected_root_domain.lower():
        return ""
    return normalized


def normalize_firecrawl_document(
    data: dict[str, object],
    requested_url: str,
    expected_root_domain: str,
) -> FirecrawlDocument:
    provider = _provider_document(data)
    metadata_value = provider.get("metadata")
    metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
    final_candidates = [
        provider.get("final_url"),
        provider.get("finalUrl"),
        metadata.get("final_url"),
        metadata.get("finalUrl"),
        provider.get("canonical_url"),
        provider.get("canonicalUrl"),
        metadata.get("canonical_url"),
        metadata.get("canonicalUrl"),
        metadata.get("canonical"),
        metadata.get("og:url"),
        metadata.get("og_url"),
        provider.get("url"),
        metadata.get("url"),
        provider.get("source_url"),
        provider.get("sourceURL"),
        metadata.get("source_url"),
        metadata.get("sourceURL"),
        requested_url,
    ]
    final_url = next(
        (url for value in final_candidates if (url := _safe_provider_url(value, expected_root_domain))),
        "",
    )
    requested = _safe_provider_url(requested_url, expected_root_domain)
    if not final_url:
        final_url = requested
    html = str(provider.get("html") or provider.get("raw_html") or provider.get("rawHtml") or "")
    markdown = str(provider.get("markdown") or "")
    return FirecrawlDocument(
        requested_url=requested,
        final_url=final_url,
        source_url=final_url or requested,
        html=html,
        markdown=markdown,
        metadata=metadata,
    )


def _metadata_text(metadata: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        text = clean(value)
        if text:
            return text
    return ""


def extract_firecrawl_document_evidence(
    document: FirecrawlDocument,
    expected_root_domain: str,
    *,
    page_kind: str = "",
) -> dict[str, object]:
    parts: list[dict[str, object]] = []
    if document.html:
        parts.append(
            extract_page_evidence(
                document.html,
                document.source_url,
                source="firecrawl",
                page_kind=page_kind,
            )
        )
    if document.markdown:
        parts.append(
            extract_markdown_evidence(
                document.markdown,
                document.source_url,
                source="firecrawl",
                page_kind=page_kind,
            )
        )

    title = _metadata_text(document.metadata, ("title", "og:title", "og_title"))
    description = _metadata_text(
        document.metadata,
        ("description", "og:description", "og_description"),
    )
    if not title:
        title = next((clean(part.get("page_title")) for part in parts if clean(part.get("page_title"))), "")
    if not description:
        description = next(
            (clean(part.get("meta_description")) for part in parts if clean(part.get("meta_description"))),
            "",
        )

    emails: dict[tuple[str, str, str], dict[str, str]] = {}
    phones: dict[tuple[str, str, str], dict[str, str]] = {}
    part_signal_values: list[object] = []
    for part in parts:
        for item in part.get("email_candidates", []):
            key = (clean(item.get("email")), clean(item.get("source_url")), clean(item.get("source")))
            if key[0]:
                emails[key] = {"email": key[0], "source_url": key[1], "source": key[2]}
        for item in part.get("phone_candidates", []):
            key = (clean(item.get("phone")), clean(item.get("source_url")), clean(item.get("source")))
            if key[0]:
                phones[key] = {"phone": key[0], "source_url": key[1], "source": key[2]}
        part_signal_values.extend(item.get("signal") for item in part.get("service_signals", []))

    links: dict[str, dict[str, object]] = {}
    for candidate in [
        *prioritized_same_domain_links(document.html, document.source_url, expected_root_domain),
        *prioritized_same_domain_markdown_links(document.markdown, document.source_url, expected_root_domain),
    ]:
        url = str(candidate["url"])
        current = links.get(url)
        if current is None or int(candidate["priority"]) < int(current["priority"]):
            links[url] = candidate

    visible_characters = max((int(part.get("visible_text_characters", 0)) for part in parts), default=0)
    service_link_signals = [
        clean(link.get("anchor_text"))
        for link in links.values()
        if clean(link.get("kind")) in {"service", "services", "inspection", "inspections"}
    ]
    ranked_signals = sorted(
        enumerate([*service_link_signals, *part_signal_values]),
        key=lambda item: (_service_signal_priority(item[1]), item[0]),
    )
    return {
        "visible_text_characters": visible_characters,
        "thin": visible_characters < THIN_TEXT_CHARACTERS,
        "anti_bot": any(bool(part.get("anti_bot")) for part in parts),
        "page_title": title,
        "meta_description": description,
        "email_candidates": sorted(emails.values(), key=lambda item: (item["email"].casefold(), item["source_url"])),
        "phone_candidates": sorted(phones.values(), key=lambda item: (item["phone"], item["source_url"])),
        "service_signals": _unique_signals(
            [title, description, *(value for _, value in ranked_signals)],
            document.source_url,
        ),
        "discovered_links": sorted(links.values(), key=lambda item: (int(item["priority"]), str(item["url"]))),
    }


def firecrawl_content(data: dict[str, object]) -> str:
    provider = _provider_document(data)
    return str(provider.get("html") or provider.get("raw_html") or provider.get("rawHtml") or provider.get("markdown") or "")


def firecrawl_source_url(data: dict[str, object], requested_url: str) -> str:
    document = normalize_firecrawl_document(data, requested_url, root_domain(requested_url))
    return document.source_url or requested_url
