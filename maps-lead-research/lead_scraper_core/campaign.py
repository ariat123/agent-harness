"""Versioned campaign configuration for neutral Google Maps discovery."""
from __future__ import annotations

import json
import ipaddress
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from lead_scraper_core.normalize import clean, root_domain


CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
LANGUAGE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
ENRICHMENT_MODES = {"off", "http", "fallback", "firecrawl"}
MIN_FIRECRAWL_CREDIT_CEILING = 5
CAMPAIGN_FIELDS = {
    "version",
    "campaign",
    "source_type",
    "country",
    "language",
    "queries",
    "locations",
    "limits",
    "website_enrichment",
    "output_root",
}
ENRICHMENT_FIELDS = {
    "mode",
    "max_companies",
    "max_http_pages_per_company",
    "max_firecrawl_companies",
    "max_firecrawl_pages_per_company",
    "max_firecrawl_credits",
    "request_timeout_seconds",
    "resume",
    "fallback_on_no_email",
    "include_domains",
    "exclude_domains",
}


@dataclass(frozen=True)
class CampaignLocation:
    type: str
    value: str


@dataclass(frozen=True)
class CampaignLimits:
    max_pages_per_query_location: int
    max_unique_results_total: int


@dataclass(frozen=True)
class WebsiteEnrichmentConfig:
    mode: str
    max_companies: int
    max_http_pages_per_company: int
    max_firecrawl_companies: int
    max_firecrawl_pages_per_company: int
    max_firecrawl_credits: int
    request_timeout_seconds: int
    resume: bool
    fallback_on_no_email: bool = False
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    configured: bool = True

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.pop("configured", None)
        data["include_domains"] = list(self.include_domains)
        data["exclude_domains"] = list(self.exclude_domains)
        return data


def off_enrichment_config(*, configured: bool = False) -> WebsiteEnrichmentConfig:
    return WebsiteEnrichmentConfig(
        mode="off",
        max_companies=1,
        max_http_pages_per_company=1,
        max_firecrawl_companies=1,
        max_firecrawl_pages_per_company=1,
        max_firecrawl_credits=1,
        request_timeout_seconds=8,
        resume=True,
        configured=configured,
    )


@dataclass(frozen=True)
class CampaignConfig:
    version: int
    campaign: str
    source_type: str
    country: str
    language: str
    queries: tuple[str, ...]
    locations: tuple[CampaignLocation, ...]
    limits: CampaignLimits
    website_enrichment: WebsiteEnrichmentConfig
    output_root: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["queries"] = list(self.queries)
        data["locations"] = [asdict(location) for location in self.locations]
        data["website_enrichment"] = (
            self.website_enrichment.to_dict()
            if self.website_enrichment.configured
            else False
        )
        return data


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def normalize_target_domain(value: object, field: str = "domain") -> str:
    text = clean(value).lower().rstrip(".")
    if not text or any(marker in text for marker in ("://", "/", "?", "#", "@", ":")) or re.search(r"\s", text):
        raise ValueError(f"{field} must be a bare public domain without a URL, path, port, or credentials")
    if text == "localhost" or text.endswith((".localhost", ".local")):
        raise ValueError(f"{field} must be a public domain")
    try:
        ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        raise ValueError(f"{field} must not be an IP address")
    labels = text.split(".")
    if len(labels) < 2 or len(text) > 253:
        raise ValueError(f"{field} is malformed")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        raise ValueError(f"{field} is malformed")
    normalized = root_domain(f"https://{text}")
    if not normalized or "." not in normalized:
        raise ValueError(f"{field} is malformed")
    return normalized


def _target_domains(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ValueError(f"website_enrichment.{field} must be a list of domains")
    return tuple(dict.fromkeys(normalize_target_domain(item, f"website_enrichment.{field}") for item in value))


def validate_website_enrichment(value: object) -> WebsiteEnrichmentConfig:
    if value is False:
        return off_enrichment_config()
    if value is True or not isinstance(value, dict):
        raise ValueError("website_enrichment must be false or a configuration object")

    unknown = set(value) - ENRICHMENT_FIELDS
    if unknown:
        raise ValueError(f"website_enrichment contains unknown fields: {sorted(unknown)}")
    missing = (ENRICHMENT_FIELDS - {"fallback_on_no_email", "include_domains", "exclude_domains"}) - set(value)
    if missing:
        raise ValueError(f"website_enrichment is missing required fields: {sorted(missing)}")

    mode = value.get("mode")
    if mode not in ENRICHMENT_MODES:
        raise ValueError(f"unsupported website_enrichment mode: {mode}")
    resume = value.get("resume")
    fallback_on_no_email = value.get("fallback_on_no_email", False)
    if not isinstance(resume, bool):
        raise ValueError("website_enrichment.resume must be true or false")
    if not isinstance(fallback_on_no_email, bool):
        raise ValueError("website_enrichment.fallback_on_no_email must be true or false")

    include_domains = _target_domains(value.get("include_domains"), "include_domains")
    exclude_domains = _target_domains(value.get("exclude_domains"), "exclude_domains")
    conflict = sorted(set(include_domains) & set(exclude_domains))
    if conflict:
        raise ValueError(f"website_enrichment domain filters conflict: {conflict}")

    config = WebsiteEnrichmentConfig(
        mode=mode,
        max_companies=_positive_int(value.get("max_companies"), "website_enrichment.max_companies"),
        max_http_pages_per_company=_positive_int(
            value.get("max_http_pages_per_company"),
            "website_enrichment.max_http_pages_per_company",
        ),
        max_firecrawl_companies=_positive_int(
            value.get("max_firecrawl_companies"),
            "website_enrichment.max_firecrawl_companies",
        ),
        max_firecrawl_pages_per_company=_positive_int(
            value.get("max_firecrawl_pages_per_company"),
            "website_enrichment.max_firecrawl_pages_per_company",
        ),
        max_firecrawl_credits=_positive_int(
            value.get("max_firecrawl_credits"),
            "website_enrichment.max_firecrawl_credits",
        ),
        request_timeout_seconds=_positive_int(
            value.get("request_timeout_seconds"),
            "website_enrichment.request_timeout_seconds",
        ),
        resume=resume,
        fallback_on_no_email=fallback_on_no_email,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        configured=True,
    )
    if config.max_firecrawl_companies > config.max_companies:
        raise ValueError("website_enrichment.max_firecrawl_companies cannot exceed max_companies")
    if config.mode in {"fallback", "firecrawl"} and config.max_firecrawl_credits < MIN_FIRECRAWL_CREDIT_CEILING:
        raise ValueError(
            f"website_enrichment.max_firecrawl_credits must be at least {MIN_FIRECRAWL_CREDIT_CEILING} "
            "for fallback or firecrawl mode"
        )
    return config


def validate_campaign_data(data: object) -> CampaignConfig:
    if not isinstance(data, dict):
        raise ValueError("campaign file must contain a JSON object")
    unknown = set(data) - CAMPAIGN_FIELDS
    if unknown:
        raise ValueError(f"campaign contains unknown fields: {sorted(unknown)}")
    if data.get("version") != 1:
        raise ValueError("version must be 1")

    campaign = data.get("campaign")
    if not isinstance(campaign, str) or not CAMPAIGN_RE.fullmatch(campaign):
        raise ValueError("campaign must contain only lowercase letters, numbers, underscores, and hyphens")
    if data.get("source_type") != "google_maps":
        raise ValueError("source_type must be google_maps")

    country = data.get("country")
    if not isinstance(country, str) or not re.fullmatch(r"[a-z]{2}", country):
        raise ValueError("country must be a two-letter lowercase code")
    language = data.get("language")
    if not isinstance(language, str) or not LANGUAGE_RE.fullmatch(language):
        raise ValueError("language must be a lowercase language code such as en or en-US")

    queries_value = data.get("queries")
    if not isinstance(queries_value, list) or not queries_value:
        raise ValueError("queries must be a non-empty list")
    queries: list[str] = []
    for query in queries_value:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("each query must be a non-empty string")
        queries.append(query.strip())

    locations_value = data.get("locations")
    if not isinstance(locations_value, list) or not locations_value:
        raise ValueError("locations must be a non-empty list")
    locations: list[CampaignLocation] = []
    for location in locations_value:
        if not isinstance(location, dict):
            raise ValueError("each location must be an object")
        location_type = location.get("type")
        value = location.get("value")
        if location_type not in {"zip", "city_state"}:
            raise ValueError("location type must be zip or city_state")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("location value must be a non-empty string")
        value = value.strip()
        if location_type == "zip" and not re.fullmatch(r"\d{5}", value):
            raise ValueError(f"invalid ZIP location: {value}")
        if location_type == "city_state":
            parts = [part.strip() for part in value.split(",")]
            if len(parts) != 2 or not parts[0] or not re.fullmatch(r"[A-Z]{2}", parts[1]):
                raise ValueError(f"city_state location must look like City, ST: {value}")
        locations.append(CampaignLocation(location_type, value))

    limits_value = data.get("limits")
    if not isinstance(limits_value, dict):
        raise ValueError("limits must be an object")
    limits = CampaignLimits(
        max_pages_per_query_location=_positive_int(
            limits_value.get("max_pages_per_query_location"),
            "limits.max_pages_per_query_location",
        ),
        max_unique_results_total=_positive_int(
            limits_value.get("max_unique_results_total"),
            "limits.max_unique_results_total",
        ),
    )

    website_enrichment = validate_website_enrichment(data.get("website_enrichment"))
    output_root = data.get("output_root", "runs")
    if not isinstance(output_root, str) or not output_root.strip():
        raise ValueError("output_root must be a non-empty path")
    output_path = Path(output_root.strip())
    if "\x00" in output_root or ".." in output_path.parts or output_path in {Path("/"), Path(".")}:
        raise ValueError("output_root is unsafe")

    return CampaignConfig(
        version=1,
        campaign=campaign,
        source_type="google_maps",
        country=country,
        language=language,
        queries=tuple(queries),
        locations=tuple(locations),
        limits=limits,
        website_enrichment=website_enrichment,
        output_root=output_root.strip(),
    )


def load_campaign(path: Path) -> CampaignConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"campaign file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid campaign JSON: {exc.msg}") from exc
    return validate_campaign_data(data)


def build_search_text(query: str, location: CampaignLocation) -> str:
    return f"{query.strip()} {location.value}".strip()
