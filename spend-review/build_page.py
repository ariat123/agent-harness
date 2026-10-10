#!/usr/bin/env python3
"""Render site/index.html from templates/page.html and out/summary.json (chart data only)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_URL = os.environ.get("REPO_URL", "https://github.com/ariat123/agent-harness/tree/main/spend-review")


def main(summary="out/summary.json", template="templates/page.html", out="site/index.html"):
    with open(os.path.join(HERE, summary)) as fh:
        s = json.load(fh)
    with open(os.path.join(HERE, template)) as fh:
        page = fh.read()
    data = json.dumps(s["usd_by_day_and_model"], separators=(",", ":"))
    page = (page.replace("__DATA__", data).replace("__REPO_URL__", REPO_URL)
            .replace("__REPO_LABEL__", REPO_URL.replace("https://", "")))
    if "\u2014" in page:
        sys.exit("em dash in page copy")
    os.makedirs(os.path.join(HERE, os.path.dirname(out)), exist_ok=True)
    with open(os.path.join(HERE, out), "w") as fh:
        fh.write(page)
    with open(os.path.join(HERE, os.path.dirname(out), "vercel.json"), "w") as fh:
        json.dump({"cleanUrls": True, "headers": [{"source": "/(.*)", "headers": [
            {"key": "X-Robots-Tag", "value": "noindex, nofollow"}]}]}, fh, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
