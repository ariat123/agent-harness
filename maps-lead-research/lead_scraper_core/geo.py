"""Generic geography parsing and validation."""
from __future__ import annotations

import math
import re

from lead_scraper_core.normalize import clean


ADDRESS_RE = re.compile(r",\s*([^,]+),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?", re.I)
CITY_STATE_RE = re.compile(r"^\s*([^,]+),\s*([A-Z]{2})\s*$", re.I)


def parse_city_state_zip(address: str, city: object = "", state: object = "", zip_code: object = "") -> tuple[str, str, str]:
    parsed_city = clean(city)
    parsed_state = clean(state).upper()
    parsed_zip = clean(zip_code)
    address_text = clean(address)

    match = ADDRESS_RE.search(address_text)
    if match:
        parsed_city = parsed_city or clean(match.group(1))
        parsed_state = parsed_state or clean(match.group(2)).upper()
        parsed_zip = parsed_zip or clean(match.group(3))

    city_state = CITY_STATE_RE.match(parsed_city)
    if city_state:
        parsed_city = clean(city_state.group(1))
        parsed_state = parsed_state or clean(city_state.group(2)).upper()

    if not parsed_zip:
        zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", address_text)
        if zip_match:
            parsed_zip = zip_match.group(1)
    return parsed_city, parsed_state, parsed_zip


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def numeric(value: object) -> float | None:
    try:
        text = clean(value).replace(",", "")
        return float(text) if text else None
    except ValueError:
        return None


def distance_to_target(row: dict[str, str], target, profile) -> float | None:
    lat = numeric(row.get("latitude"))
    lng = numeric(row.get("longitude"))
    if lat is None or lng is None:
        return None
    target_coords = profile.ZIP_COORDS.get(target.zip)
    if not target_coords:
        return None
    return haversine_miles(target_coords[0], target_coords[1], lat, lng)


def classify_geo(row: dict[str, str], target, profile, all_target_zips: set[str]) -> tuple[str, str]:
    state = clean(row.get("state")).upper()
    zip_code = clean(row.get("zip_code"))
    city = clean(row.get("city")).lower()
    nearby_zips = set(profile.nearby_zips_for(target.metro))
    nearby_cities = set(profile.nearby_city_terms_for(target.metro))

    if zip_code and zip_code in all_target_zips:
        return "in_target_zip", ""
    if zip_code and zip_code == target.zip:
        return "in_target_zip", ""
    if state and state != target.state:
        return "out_of_geo", f"state {state} != target state {target.state}"
    if zip_code and zip_code in nearby_zips:
        return "nearby_target_metro", f"nearby ZIP {zip_code} accepted for {target.metro}"

    distance = distance_to_target(row, target, profile)
    if distance is not None:
        if distance <= profile.NEARBY_DISTANCE_MILES:
            return "nearby_target_metro", f"{distance:.1f} miles from source ZIP {target.zip}"
        if state and state == target.state:
            return "in_target_state", f"{distance:.1f} miles from source ZIP {target.zip}; state matches"
        return "out_of_geo", f"{distance:.1f} miles from source ZIP {target.zip}"

    if state and state == target.state:
        if city and any(term in city for term in nearby_cities):
            return "nearby_target_metro", f"city {row.get('city')} accepted for {target.metro}"
        return "in_target_state", "state matches but ZIP/distance not enough for metro"
    if not state and not zip_code:
        return "unknown_geo", "missing parsed state and ZIP"
    return "unknown_geo", "insufficient location data"

