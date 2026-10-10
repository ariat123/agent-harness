"""Versioned, offline US city expansion, interleaved by Census region and state."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def secondary_catalog():
    return json.loads((Path(__file__).parent / "data/us_places_2025.json").read_text())


# Intentional curated search markets, not an exhaustive population dataset.
REGIONS = {
    "Northeast": "NY PA MA NJ CT NH ME RI VT",
    "Midwest": "IL OH MI MN MO WI IN IA KS NE SD ND",
    "South": "TX FL GA NC VA TN MD SC AL LA KY OK AR MS WV DE DC",
    "West": "CA WA AZ CO OR NV UT NM ID HI MT AK WY",
}
CITY_DATA = """NY|New York|Buffalo|Rochester
PA|Philadelphia|Pittsburgh|Allentown
MA|Boston|Worcester|Springfield
NJ|Newark|Jersey City|Trenton
CT|Bridgeport|Hartford|New Haven
NH|Manchester|Nashua|Concord
ME|Portland|Bangor|Lewiston
RI|Providence|Warwick|Cranston
VT|Burlington|Rutland|Montpelier
IL|Chicago|Rockford|Springfield
OH|Columbus|Cleveland|Cincinnati
MI|Detroit|Grand Rapids|Lansing
MN|Minneapolis|Rochester|Duluth
MO|Kansas City|St. Louis|Springfield
WI|Milwaukee|Madison|Green Bay
IN|Indianapolis|Fort Wayne|Evansville
IA|Des Moines|Cedar Rapids|Davenport
KS|Wichita|Overland Park|Topeka
NE|Omaha|Lincoln|Grand Island
SD|Sioux Falls|Rapid City|Aberdeen
ND|Fargo|Bismarck|Grand Forks
TX|Houston|Dallas|Austin|San Antonio|El Paso
FL|Miami|Tampa|Orlando|Jacksonville
GA|Atlanta|Savannah|Augusta
NC|Charlotte|Raleigh|Greensboro
VA|Virginia Beach|Richmond|Arlington
TN|Nashville|Memphis|Knoxville
MD|Baltimore|Frederick|Rockville
SC|Charleston|Columbia|Greenville
AL|Birmingham|Huntsville|Mobile
LA|New Orleans|Baton Rouge|Shreveport
KY|Louisville|Lexington|Bowling Green
OK|Oklahoma City|Tulsa|Norman
AR|Little Rock|Fayetteville|Fort Smith
MS|Jackson|Gulfport|Hattiesburg
WV|Charleston|Huntington|Morgantown
DE|Wilmington|Dover|Newark
DC|Washington
CA|Los Angeles|San Francisco|San Diego|Sacramento|Fresno
WA|Seattle|Spokane|Tacoma
AZ|Phoenix|Tucson|Flagstaff
CO|Denver|Colorado Springs|Fort Collins
OR|Portland|Eugene|Salem
NV|Las Vegas|Reno|Carson City
UT|Salt Lake City|Provo|Ogden
NM|Albuquerque|Santa Fe|Las Cruces
ID|Boise|Idaho Falls|Coeur d'Alene
HI|Honolulu|Hilo|Kahului
MT|Billings|Missoula|Bozeman
AK|Anchorage|Fairbanks|Juneau
WY|Cheyenne|Casper|Laramie"""
CITIES = {r.split("|")[0]: r.split("|")[1:] for r in CITY_DATA.splitlines()}
STATE_REGION = {s: r for r, ss in REGIONS.items() for s in ss.split()}
STATE_NAMES = dict(
    zip(
        "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming".upper().split(
            "|"
        ),
        "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
    )
)


def plan(config):
    g = config["geography"]
    mode = g["mode"]
    allowed = set(CITIES)
    if mode == "states":
        allowed &= set(g["states"])
    if mode == "regions":
        allowed &= {s for s, r in STATE_REGION.items() if r in g["regions"]}
    if g["include_states"]:
        allowed &= set(g["include_states"])
    allowed -= set(g["exclude_states"])
    targets = []
    seen = set()

    def add(value, state="", tier=0):
        if state and state not in allowed:
            return
        if value.casefold() not in seen:
            seen.add(value.casefold())
            targets.append(
                {
                    "value": value,
                    "state": state,
                    "region": STATE_REGION.get(state, ""),
                    "tier": tier,
                }
            )

    for value in g["targets"]:
        state = (
            value.rsplit(",", 1)[-1].strip().upper()
            if config["country"] == "US" and "," in value
            else ""
        )
        add(value, state)
    if mode in {"states", "regions", "nationwide"}:
        for tier in range(max(map(len, CITIES.values()))):
            for state_index in range(max(len(s.split()) for s in REGIONS.values())):
                for states in REGIONS.values():
                    ss = states.split()
                    if state_index < len(ss):
                        s = ss[state_index]
                        if tier < len(CITIES[s]):
                            add(f"{CITIES[s][tier]}, {s}", s, tier)
        # One candidate market per possible request permits continued expansion
        # even when every preceding first page was empty. Never query ZIPs here.
        limit = max(len(targets), config["max_maps_requests"])
        catalog = secondary_catalog()["places"]
        for depth in range(max((len(catalog[s]) for s in allowed), default=0)):
            for state_index in range(
                max(len(states.split()) for states in REGIONS.values())
            ):
                for states in REGIONS.values():
                    ordered = states.split()
                    if state_index < len(ordered):
                        state = ordered[state_index]
                        if state in allowed and depth < len(catalog[state]):
                            add(f"{catalog[state][depth]}, {state}", state, depth + 5)
                    if len(targets) >= limit:
                        break
                if len(targets) >= limit:
                    break
            if len(targets) >= limit:
                break
    return {
        "version": 2,
        "country": config["country"],
        "targets": targets,
        "planned_states": sorted({t["state"] for t in targets if t["state"]}),
        "planned_regions": sorted({t["region"] for t in targets if t["region"]}),
        "secondary_catalog": "US Census 2025 National Places Gazetteer",
        "exhaustion_policy": "expand through bundled Census places to the request ceiling; report shortfall if the complete catalog and configured pages are exhausted",
    }
