"""Strict v2 campaign contract. Validation never loads credentials or uses network."""

import copy
import json
import re
from pathlib import Path

from .geography import CITIES, REGIONS, plan

ROLES = [
    "owner",
    "founder",
    "president",
    "ceo",
    "principal",
    "general_manager",
    "regional_manager",
    "operations",
    "sales",
    "marketing",
]
DEFAULTS = {
    "version": 2,
    "campaign": "",
    "business_category": "",
    "queries": [],
    "suggest_related_queries": False,
    "country": "US",
    "language": "en",
    "geography": {
        "mode": "nationwide",
        "targets": [],
        "states": [],
        "regions": [],
        "include_states": [],
        "exclude_states": [],
    },
    "max_net_new_companies": 100,
    "min_reviews": 5,
    "max_reviews": None,
    "max_contacts_per_company": 2,
    "max_emails_per_company": 2,
    "max_maps_requests": 100,
    "max_pages_per_search": 3,
    "maps_interval_seconds": 1,
    "max_retries": 3,
    "max_firecrawl_credit_delta": 0,
    "firecrawl_safety_reserve": 10,
    "max_firecrawl_pages_per_domain": 2,
    "max_http_pages_per_domain": 1,
    "decision_maker_roles": ROLES,
    "resume_policy": "retry_failed",
    "refresh_research": False,
    "output_directory": "runs/maps-research",
    "historical_registry_path": "data/research-registry.sqlite",
    "suppression_path": None,
    "continuation_run": None,
}


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("Campaign must be an object")
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ValueError("Unknown campaign fields: " + ", ".join(sorted(unknown)))
    c = copy.deepcopy(DEFAULTS)
    c.update(data)
    if type(c["version"]) is not int or c["version"] != 2:
        raise ValueError("version must be 2 (legacy v1 uses company_discovery.py)")
    if not isinstance(c["campaign"], str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,79}", c["campaign"]
    ):
        raise ValueError("campaign must be a lowercase slug")
    if (
        not isinstance(c["business_category"], str)
        or not c["business_category"].strip()
    ):
        raise ValueError("business_category is required")
    for field in ["queries", "decision_maker_roles"]:
        if not isinstance(c[field], list) or any(
            not isinstance(v, str) or not v.strip() for v in c[field]
        ):
            raise ValueError(field + " must be a list of nonempty strings")
        c[field] = list(dict.fromkeys(v.strip() for v in c[field]))
    if not c["queries"]:
        c["queries"] = [c["business_category"]]
    if not c["decision_maker_roles"] or set(c["decision_maker_roles"]) - set(ROLES):
        raise ValueError("decision_maker_roles must use supported role categories")
    for f in ["suggest_related_queries", "refresh_research"]:
        if type(c[f]) is not bool:
            raise ValueError(f + " must be boolean")
    for f in [
        "max_net_new_companies",
        "max_maps_requests",
        "max_pages_per_search",
        "max_contacts_per_company",
        "max_emails_per_company",
    ]:
        if type(c[f]) is not int or c[f] < 1:
            raise ValueError(f + " must be a positive integer")
    for f in [
        "min_reviews",
        "max_firecrawl_credit_delta",
        "firecrawl_safety_reserve",
        "max_firecrawl_pages_per_domain",
        "max_http_pages_per_domain",
        "max_retries",
    ]:
        if type(c[f]) is not int or c[f] < 0:
            raise ValueError(f + " must be a nonnegative integer")
    if c["max_reviews"] is not None and (
        type(c["max_reviews"]) is not int or c["max_reviews"] < c["min_reviews"]
    ):
        raise ValueError("max_reviews must be >= min_reviews")
    if (
        type(c["maps_interval_seconds"]) not in (int, float)
        or not 0 <= c["maps_interval_seconds"] <= 60
    ):
        raise ValueError("maps_interval_seconds must be 0..60")
    if c["resume_policy"] not in ["retry_failed", "skip_failed"]:
        raise ValueError("Invalid resume_policy")
    if not isinstance(c["country"], str) or not re.fullmatch("[A-Z]{2}", c["country"]):
        raise ValueError("country must be uppercase ISO alpha-2")
    if not isinstance(c["language"], str) or not re.fullmatch(
        "[a-z]{2}(?:-[A-Z]{2})?", c["language"]
    ):
        raise ValueError("Invalid language")
    g = data.get("geography", {})
    if not isinstance(g, dict) or set(g) - set(DEFAULTS["geography"]):
        raise ValueError("Invalid geography fields")
    c["geography"] = {**DEFAULTS["geography"], **g}
    g = c["geography"]
    if g["mode"] not in ["zip_codes", "cities", "states", "regions", "nationwide"]:
        raise ValueError("Invalid geography mode")
    for field in ["targets", "states", "regions", "include_states", "exclude_states"]:
        if not isinstance(g[field], list) or any(
            not isinstance(v, str) or not v for v in g[field]
        ):
            raise ValueError("Invalid geography." + field)
        g[field] = list(dict.fromkeys(g[field]))
    for f in ["states", "include_states", "exclude_states"]:
        if set(g[f]) - set(CITIES):
            raise ValueError("Unknown US state in " + f)
    if set(g["regions"]) - set(REGIONS):
        raise ValueError("Unknown Census region")
    if set(g["include_states"]) & set(g["exclude_states"]):
        raise ValueError("Conflicting state filters")
    if c["country"] != "US" and (
        g["mode"] not in ["cities"]
        or any(g[f] for f in ["states", "regions", "include_states", "exclude_states"])
    ):
        raise ValueError(
            "Outside US use explicit cities; automatic expansion is US-only"
        )
    if g["mode"] in ["zip_codes", "cities"] and not g["targets"]:
        raise ValueError("Explicit targets required")
    if g["mode"] == "zip_codes" and any(
        not re.fullmatch(r"\d{5}", v) for v in g["targets"]
    ):
        raise ValueError("ZIP targets must have five digits")
    if g["mode"] == "states" and not g["states"]:
        raise ValueError("states required")
    if g["mode"] == "regions" and not g["regions"]:
        raise ValueError("regions required")
    if c["country"] == "US" and g["mode"] != "zip_codes":
        if any(
            not re.fullmatch(r".+, [A-Z]{2}", v) or v[-2:] not in CITIES
            for v in g["targets"]
        ):
            raise ValueError("City targets must look like City, ST")
    for f in [
        "output_directory",
        "historical_registry_path",
        "suppression_path",
        "continuation_run",
    ]:
        v = c[f]
        if v is None and f in {"suppression_path", "continuation_run"}:
            continue
        if (
            not isinstance(v, str)
            or not v.strip()
            or "\x00" in v
            or ".." in Path(v).parts
            or Path(v) in [Path("/"), Path(".")]
        ):
            raise ValueError("Unsafe " + f)
    if (
        Path(c["output_directory"]).resolve()
        == Path(c["historical_registry_path"]).resolve()
    ):
        raise ValueError("Output and registry must differ")
    if not plan(c)["targets"]:
        raise ValueError("Geography filters leave no targets")
    return c


def load(path):
    return validate(json.loads(Path(path).read_text()))


def queries(c):
    # Optional neutral literal variants; no niche-specific offer/ICP assumptions.
    base = re.sub(
        r"\s+(?:companies|company|services|service)$",
        "",
        c["business_category"].strip(),
        flags=re.I,
    )
    return list(
        dict.fromkeys(
            c["queries"]
            + (
                [
                    base + " services",
                    base + " company",
                ]
                if c["suggest_related_queries"]
                else []
            )
        )
    )
