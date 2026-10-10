#!/usr/bin/env python3
"""Check an Account Profile draft against the skill's structural rules.

Usage: python3 evals/check_profile.py samples/*.md

Exits 1 if any profile fails. Warnings don't fail the run; a person reads them.
This checks structure and sourcing discipline, not whether a fact is true.
Truth is checked by opening each source (see rerun-checklist.md).
"""
import re
import sys
from datetime import date

QUESTIONS = [
    "1. Goals and strategic initiatives",
    "2. Macro factors",
    "3. Competitors and share",
    "4. Biggest risks",
    "5. How IT supports or hinders the strategy",
    "6. Digital transformation strategy",
    "7. How IT is perceived",
    "8. Current GitLab relationship",
]
REQUIRED = QUESTIONS + [
    "Gaps to close with the account team",
    "Questions to confirm with your champion",
    "Held",
    "Sources",
]
VALUE_DRIVERS = [
    "Increase Operational Efficiencies",
    "Deliver Better Products Faster",
    "Reduce Security and Compliance Risk",
    "Accelerate Cloud Adoption & Transformation",
]
CITE = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\]")
# Role word next to a capitalized First Last, e.g. "CEO Jane Smith" or "Jane Smith, CFO".
ROLE = r"(?:CEO|CFO|CIO|CTO|CISO|COO|President|Chair|Chairman|Chairwoman|Officer|Director)"
NAME = r"[A-Z][a-z]+(?:\s[A-Z]\.)?\s[A-Z][a-z]+"
NAME_PATTERNS = [
    re.compile(rf"\b{ROLE},?\s+{NAME}\b"),
    re.compile(rf"\b{NAME},\s+(?:the\s+)?(?:company's\s+)?{ROLE}\b"),
    re.compile(r"\b(?:Mr|Ms|Mrs|Dr)\.\s+[A-Z][a-z]+"),
]
STALE_DAYS = 540  # about 18 months
MAX_WORDS = 800  # one page before the Sources table


def sections(text):
    out, current = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            out[current] = []
        elif current:
            out[current].append(line)
    return out


def bullets(lines):
    return [l.strip() for l in lines if re.match(r"\s*(?:[-*]|\d+\.)\s+", l)]


def check(path):
    text = open(path, encoding="utf-8").read()
    fails, warns = [], []
    secs = sections(text)

    if not re.search(r"^# Account Profile: .+", text, re.M):
        fails.append("missing '# Account Profile: <Company>' title")
    if "Data tier:" not in text:
        fails.append("missing 'Data tier:' label")

    headings = list(secs)
    for h in REQUIRED:
        if h not in secs:
            fails.append(f"missing section '{h}'")
    order = [h for h in headings if h in QUESTIONS]
    if order != [q for q in QUESTIONS if q in secs]:
        fails.append("question sections out of handbook order")

    # Sources table: ID -> (published, url, accessed)
    sources = {}
    for line in secs.get("Sources", []):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and re.fullmatch(r"S\d+", cells[0]):
            sources[cells[0]] = cells
    if not sources:
        fails.append("no sources in the Sources table")
    for sid, cells in sources.items():
        pub, url, acc = cells[2], cells[3], cells[4]
        if not url.startswith("https://"):
            fails.append(f"{sid}: URL missing or not https")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", acc):
            fails.append(f"{sid}: accessed date missing")
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", pub)
        if not m:
            warns.append(f"{sid}: published date not in YYYY-MM-DD form ('{pub}')")
        else:
            age = (date.today() - date(*map(int, m.groups()))).days
            if age > STALE_DAYS:
                warns.append(f"{sid}: source is {age} days old")

    # Every bullet in questions 1-6 carries a citation; question 7 may say not public.
    cited = set()
    for q in QUESTIONS:
        for b in bullets(secs.get(q, [])):
            ids = [i.strip() for m in CITE.findall(b) for i in m.split(",")]
            cited.update(ids)
            exempt = (
                "Not in public sources" in b
                or b.lstrip("-* ").startswith("Ask:")
                or "AE to fill" in b
            )
            if not ids and not exempt:
                fails.append(f"[{q[:2].strip()}] uncited line: {b[:70]}")
            for i in ids:
                if i not in sources:
                    fails.append(f"[{q[:2].strip()}] cites {i}, which isn't in Sources")
    body = text.split("## Sources")[0]
    all_cited = {i.strip() for m in CITE.findall(body) for i in m.split(",")}
    for i in sources:
        if i not in all_cited:
            warns.append(f"{i} is listed but never cited")

    # Value drivers: at most two, exact names only.
    q1 = "\n".join(secs.get(QUESTIONS[0], []))
    vd_block = q1.split("Value drivers most likely to matter")[-1] if "Value drivers most likely to matter" in q1 else ""
    if not vd_block:
        fails.append("missing 'Value drivers most likely to matter' under question 1")
    else:
        vd_lines = [l for l in vd_block.splitlines() if re.match(r"\s*\d+\.\s", l)]
        if not 1 <= len(vd_lines) <= 2:
            fails.append(f"expected 1-2 value drivers, found {len(vd_lines)}")
        for l in vd_lines:
            if not any(v in l for v in VALUE_DRIVERS):
                fails.append(f"value driver not one of the four handbook names: {l.strip()[:60]}")
            if not CITE.search(l):
                fails.append(f"value driver without a citation: {l.strip()[:60]}")

    # Question 8: a customer claim needs a public GitLab customer story.
    for b in bullets(secs.get(QUESTIONS[7], [])):
        if "AE to fill" in b:
            continue
        ids = [i.strip() for m in CITE.findall(b) for i in m.split(",")]
        if not any(i in sources and "about.gitlab.com/customers" in sources[i][3] for i in ids):
            fails.append(f"[8] relationship line without a public GitLab customer story: {b[:60]}")

    # Question 7 should rarely be answerable.
    q7 = "\n".join(secs.get(QUESTIONS[6], []))
    if "Not in public sources" not in q7:
        warns.append("question 7 answered from sources; confirm the source says it directly")

    # Personal names.
    for pat in NAME_PATTERNS:
        for m in pat.finditer(text):
            fails.append(f"possible personal name: '{m.group(0)}'")

    # Inference lines must be labeled.
    for q in QUESTIONS[:6]:
        for b in bullets(secs.get(q, [])):
            if re.search(r"\b(likely|probably|suggests|appears to|may be)\b", b) and "Read:" not in b:
                warns.append(f"[{q[:2].strip()}] sounds like inference but isn't labeled 'Read:': {b[:60]}")

    words = len(text.split("## Sources")[0].split())
    if words > MAX_WORDS:
        warns.append(f"{words} words before Sources; one page is about {MAX_WORDS}")

    champ = bullets(secs.get("Questions to confirm with your champion", []))
    if not 3 <= len(champ) <= 5:
        fails.append(f"expected 3-5 champion questions, found {len(champ)}")

    return fails, warns


def main(paths):
    if not paths:
        print(__doc__)
        return 2
    bad = 0
    for p in paths:
        fails, warns = check(p)
        status = "PASS" if not fails else "FAIL"
        print(f"{status}  {p}  ({len(fails)} failures, {len(warns)} warnings)")
        for f in fails:
            print(f"   FAIL  {f}")
        for w in warns:
            print(f"   warn  {w}")
        bad += bool(fails)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
