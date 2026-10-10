"""Build the bundled offline secondary-market catalog from a pinned Census ZIP.

Maintainer usage: python -m maps_lead_research.catalog INPUT.zip --output OUTPUT.json
This command does not download data or make provider requests.
"""

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

SOURCE_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip"


def build(source, output):
    from .geography import CITIES

    content = Path(source).read_bytes()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        text = archive.read("2025_Gaz_place_national.txt").decode("utf-8-sig")
    cities = {state: set() for state in CITIES}
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        state = row["USPS"].strip()
        if state not in cities:
            continue
        name = re.sub(
            r" (?:city and borough|consolidated government \(balance\)|metropolitan government \(balance\)|metro government \(balance\)|municipality|city|town|village|borough|CDP)$",
            "",
            row["NAME"].strip(),
        )
        if name:
            cities[state].add(name)
    catalog = {
        "version": 1,
        "source": SOURCE_URL,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "source_year": 2025,
        "places": {state: sorted(names) for state, names in cities.items()},
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(
        json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = build(args.source, args.output)
    print(
        json.dumps(
            {
                "states": len(catalog["places"]),
                "places": sum(map(len, catalog["places"].values())),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
