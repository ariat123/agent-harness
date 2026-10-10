"""Shared enrichment helpers for lead discovery, contact extraction, and scoring."""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    import dns.resolver
except Exception:  # pragma: no cover - optional until requirements are installed.
    dns = None
else:
    dns = dns


TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}

NON_SITE_DOMAIN_SUFFIXES = {
    "facebook.com",
    "fb.me",
    "instagram.com",
    "yelp.com",
    "linktr.ee",
    "bio.site",
    "beacons.ai",
    "flow.page",
    "google.com",
    "goo.gl",
    "g.page",
    "business.site",
}

FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "aol.com",
    "msn.com",
    "me.com",
    "mac.com",
    "live.com",
    "ymail.com",
    "proton.me",
    "protonmail.com",
    "comcast.net",
    "sbcglobal.net",
    "verizon.net",
    "att.net",
}

ROLE_EMAIL_LOCALS = {
    "info",
    "hello",
    "contact",
    "frontdesk",
    "front-desk",
    "appointments",
    "appointment",
    "booking",
    "bookings",
    "office",
    "admin",
    "support",
    "reception",
    "patients",
    "patient",
    "care",
    "team",
    "schedule",
    "scheduling",
}

BAD_EMAIL_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "ico"}
DEFAULT_BAD_EMAIL_LOCALS = {
    "demo",
    "example",
    "test",
    "sample",
    "yourname",
    "yourcompany",
    "you",
    "name",
    "noreply",
    "no-reply",
    "donotreply",
    "john.doe",
    "jane.doe",
    "first.last",
    "firstname.lastname",
}
DEFAULT_BAD_EMAIL_DOMAINS = {
    "example.com",
    "yourcompany.com",
    "yoursite.com",
    "domain.com",
    "email.com",
    "yourdomain.com",
    "placeholder.com",
    "schema.org",
    "wix.com",
    "wixpress.com",
    "squarespace.com",
    "webflow.com",
    "shopify.com",
    "mailchimp.com",
    "klaviyo.com",
}

AGENCY_CONTEXT_RE = re.compile(
    r"\b(website by|site by|designed by|developed by|powered by|marketing by|"
    r"digital agency|creative agency|web design|seo agency|branding agency)\b",
    re.I,
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(
    r"(?:\+?1[\s.\-()]*)?(?:\(?[2-9]\d{2}\)?[\s.\-]*)[2-9]\d{2}[\s.\-]*\d{4}"
)


@dataclass(frozen=True)
class EmailCandidate:
    email: str
    source: str
    context: str = ""


def json_dumps(value: object) -> str | None:
    if value is None or value == "":
        return None
    return json.dumps(value, sort_keys=True)


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.netloc.lower().strip()
    if not host:
        return None
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_KEYS
        ]
    )
    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))


def domain_suffix(host: str, suffix: str) -> bool:
    host = host.lower().removeprefix("www.")
    suffix = suffix.lower().removeprefix("www.")
    return host == suffix or host.endswith("." + suffix)


def url_host(url: str | None) -> str:
    normalized = normalize_url(url)
    if not normalized:
        return ""
    return urlparse(normalized).netloc.lower().removeprefix("www.")


def root_domain(host: str) -> str:
    host = host.lower().removeprefix("www.")
    # Use the bundled suffix snapshot offline; co.uk and hosted tenants must
    # not collapse unrelated businesses or break same-domain HTTP checks.
    import tldextract

    if not hasattr(root_domain, "_extractor"):
        root_domain._extractor = tldextract.TLDExtract(
            suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
        )
    result = root_domain._extractor(host)
    return result.top_domain_under_public_suffix or host


def is_non_site_url(url: str | None) -> bool:
    normalized = normalize_url(url)
    if not normalized:
        return False
    parsed = urlparse(normalized)
    host = parsed.netloc.lower().removeprefix("www.")
    for suffix in NON_SITE_DOMAIN_SUFFIXES:
        if not domain_suffix(host, suffix):
            continue
        if suffix == "google.com":
            return "/maps" in parsed.path or "business" in parsed.path
        return True
    return False


def extract_instagram_handle(value: object) -> str | None:
    text = "" if value is None else str(value)
    match = re.search(
        r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})",
        text,
        re.I,
    )
    if not match:
        return None
    handle = match.group(1).strip(" ./")
    if handle.lower() in {"p", "reel", "stories", "explore", "accounts"}:
        return None
    return handle or None


def website_state(url: str | None) -> dict[str, object]:
    normalized = normalize_url(url)
    return {
        "website": normalized,
        "has_real_website": bool(normalized and not is_non_site_url(normalized)),
        "instagram_handle": extract_instagram_handle(url),
    }


def normalize_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def name_address_key(name: object, address: object) -> str | None:
    name_key = normalize_key(name)
    address_key = normalize_key(address)
    if not name_key or not address_key:
        return None
    return f"{name_key}|{address_key}"


def norm_email(value: object) -> str:
    text = html.unescape("" if value is None else str(value)).strip().lower()
    text = text.removeprefix("mailto:")
    text = text.split("?", 1)[0]
    text = text.replace("%20", "").strip(".,;:()[]{}<>\"'")
    if not EMAIL_RE.fullmatch(text):
        return ""
    local, domain = text.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return ""
    if domain.rsplit(".", 1)[-1] in BAD_EMAIL_TLDS:
        return ""
    return text


def email_context(text: str, email: str, window: int = 180) -> str:
    lower = text.lower()
    pos = lower.find(email.lower())
    if pos < 0:
        return ""
    start = max(0, pos - window)
    end = min(len(text), pos + len(email) + window)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def is_blocked_email(
    email: str,
    context: str = "",
    blocked_locals: set[str] | None = None,
    blocked_domains: set[str] | None = None,
) -> bool:
    email = norm_email(email)
    if not email:
        return True
    local, domain = email.rsplit("@", 1)
    blocked_locals = (blocked_locals or set()) | DEFAULT_BAD_EMAIL_LOCALS
    blocked_domains = (blocked_domains or set()) | DEFAULT_BAD_EMAIL_DOMAINS
    if local in blocked_locals or domain in blocked_domains:
        return True
    if AGENCY_CONTEXT_RE.search(context or ""):
        return True
    return False


def email_type(email: str, owner_name: str | None = None) -> str:
    local, domain = email.rsplit("@", 1)
    local_clean = re.sub(r"[^a-z]", "", local)
    if owner_name:
        parts = [re.sub(r"[^a-z]", "", p.lower()) for p in owner_name.split()]
        parts = [p for p in parts if p]
        if parts and local_clean in {parts[0], "".join(parts[:2])}:
            return "owner_matched"
    if local in ROLE_EMAIL_LOCALS or local_clean in {re.sub(r"[^a-z]", "", x) for x in ROLE_EMAIL_LOCALS}:
        return "role"
    if domain in FREE_EMAIL_DOMAINS:
        return "webmail"
    return "direct"


def validate_mx(domain: str) -> bool | None:
    if dns is None:
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=4)
        return bool(list(answers))
    except Exception:
        try:
            answers = dns.resolver.resolve(domain, "A", lifetime=4)
            return bool(list(answers))
        except Exception:
            return False


def rank_email_candidates(
    candidates: list[EmailCandidate | dict[str, object]],
    owner_name: str | None = None,
    blocked_locals: set[str] | None = None,
    blocked_domains: set[str] | None = None,
    validate_dns: bool = True,
) -> dict[str, object] | None:
    ranked: list[tuple[int, dict[str, object]]] = []
    seen: set[str] = set()
    for raw in candidates:
        if isinstance(raw, EmailCandidate):
            candidate = raw
        else:
            candidate = EmailCandidate(
                email=str(raw.get("email") or ""),
                source=str(raw.get("source") or "text"),
                context=str(raw.get("context") or ""),
            )
        email = norm_email(candidate.email)
        if not email or email in seen:
            continue
        seen.add(email)
        if is_blocked_email(email, candidate.context, blocked_locals, blocked_domains):
            continue
        local, domain = email.rsplit("@", 1)
        mx_valid = validate_mx(domain) if validate_dns else None
        if mx_valid is False:
            continue
        etype = email_type(email, owner_name=owner_name)
        score = {"owner_matched": 100, "direct": 75, "role": 70, "webmail": 45}.get(etype, 40)
        if candidate.source == "mailto":
            score += 18
        elif candidate.source in {"firecrawl", "firecrawl_email"}:
            score += 10
        elif candidate.source == "search":
            score -= 8
        if mx_valid is True:
            score += 8
        confidence = "high" if score >= 95 else ("medium" if score >= 65 else "low")
        ranked.append(
            (
                score,
                {
                    "email": email,
                    "email_type": etype,
                    "email_confidence": confidence,
                    "email_source": candidate.source,
                    "email_mx_valid": mx_valid,
                    "email_context": candidate.context,
                    "score": score,
                },
            )
        )
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def normalize_role_class(raw_role: str | None, context: str = "") -> str:
    text = f"{raw_role or ''} {context or ''}".lower()
    if not text.strip():
        return "unknown"
    if any(term in text for term in ("medical director", "physician", "doctor", " m.d", " md", " d.o", " do ")):
        return "clinical_lead_or_credentialing_md"
    if any(term in text for term in ("owner", "founder", "co-founder", "cofounder", "ceo", "president", "principal")):
        return "operating_owner"
    if any(term in text for term in ("practice manager", "clinic manager", "office manager", "manager", "director")):
        return "operating_owner"
    if any(term in text for term in ("injector", "provider", "nurse", "np", "pa-c", "rn", "dermatologist", "surgeon")):
        return "clinical_lead_or_credentialing_md"
    return "unknown"


SERVICE_PATTERNS = {
    "botox": r"\bbotox\b",
    "dysport": r"\bdysport\b",
    "daxxify": r"\bdaxxify\b",
    "filler": r"\b(dermal filler|fillers?|juvederm|restylane|versa|radiesse|sculptra)\b",
    "laser": r"\b(laser|ipl|bbl)\b",
    "rf_microneedling": r"\b(rf microneedling|morpheus8|secret rf|potenza)\b",
    "hydrafacial": r"\b(hydrafacial|hydra facial)\b",
    "iv_therapy": r"\b(iv therapy|iv hydration|vitamin drip|nad\+?)\b",
    "weight_loss": r"\b(glp-?1|semaglutide|tirzepatide|medical weight loss|weight loss)\b",
    "hormone_trt": r"\b(hormone|trt|testosterone|hrt|bioidentical)\b",
    "prp": r"\b(prp|platelet rich plasma)\b",
}

BOOKING_PATTERNS = {
    "Boulevard": r"\b(blvd|boulevard)\b|joinboulevard\.com",
    "Vagaro": r"vagaro\.com|\bvagaro\b",
    "Mindbody": r"mindbodyonline\.com|\bmindbody\b",
    "Aesthetic Record": r"aestheticrecord\.com|\baesthetic record\b",
    "Zenoti": r"zenoti\.com|\bzenoti\b",
    "Jane": r"janeapp\.com|\bjane app\b",
    "Healthie": r"gethealthie\.com|\bhealthie\b",
    "Moxie": r"joinmoxie\.com|\bmoxie\b",
}


def extract_service_tags(text: object) -> list[str]:
    blob = str(text or "").lower()
    tags = [tag for tag, pattern in SERVICE_PATTERNS.items() if re.search(pattern, blob, re.I)]
    return sorted(tags)


def detect_booking_tech(text: object) -> str:
    blob = str(text or "")
    found = [name for name, pattern in BOOKING_PATTERNS.items() if re.search(pattern, blob, re.I)]
    return "|".join(found)


def phone_digits(value: object) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) >= 10:
        return digits[-10:]
    return ""


def extract_phone_numbers(text: object) -> list[str]:
    seen: list[str] = []
    for match in PHONE_RE.findall(str(text or "")):
        digits = phone_digits(match)
        if digits and digits not in seen:
            seen.append(digits)
    return seen


def compute_data_completeness(row: dict[str, object]) -> int:
    weighted_fields = [
        ("place_id", 8),
        ("name", 8),
        ("address", 8),
        ("phone", 8),
        ("website", 8),
        ("email", 12),
        ("email_confidence", 5),
        ("decision_maker_name", 10),
        ("owner_confidence", 5),
        ("instagram_handle", 4),
        ("booking_tech", 4),
        ("service_tags", 8),
        ("rating", 4),
        ("review_count", 4),
        ("categories", 4),
    ]
    score = 0
    for field, weight in weighted_fields:
        value = row.get(field)
        if value not in (None, "", [], "unknown", "unresolved"):
            score += weight
    return min(100, score)


def icp_tier_for(row: dict[str, object]) -> str:
    if str(row.get("dq_flag") or "").lower() in {"1", "true", "yes"}:
        return "low"
    review_count = int(row.get("review_count") or row.get("google_reviews") or 0)
    rating = float(row.get("rating") or row.get("google_rating") or 0)
    has_site = bool(row.get("has_real_website"))
    services = str(row.get("service_tags") or "")
    email_conf = str(row.get("email_confidence") or "")
    if has_site and review_count >= 50 and rating >= 4.5 and services and email_conf in {"high", "medium"}:
        return "A"
    if has_site and review_count >= 10 and rating >= 4.0:
        return "B"
    return "low"
