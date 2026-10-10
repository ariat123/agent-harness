"""Conservative, source-backed people, service evidence and email candidates."""

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .common import domain, norm, stable_id

ROLE_PATTERNS = {
    "owner": r"(?:Co[- ]?)?Owner",
    "founder": r"(?:Co[- ]?)?Founder",
    "president": r"President",
    "ceo": r"CEO|Chief Executive Officer",
    "principal": r"(?:Managing )?Principal",
    "general_manager": r"General Manager",
    "regional_manager": r"(?:Regional|Branch|Area) (?:Manager|Director)",
    "operations": r"(?:Chief Operating Officer|COO|(?:VP|Vice President|Director|Head|Manager) (?:of )?Operations|Operations (?:Director|Manager))",
    "sales": r"(?:(?:VP|Vice President|Director|Head|Manager) (?:of )?(?:Sales|Business Development)|(?:Sales|Business Development) (?:Director|Manager))",
    "marketing": r"(?:(?:VP|Vice President|Director|Head|Manager) (?:of )?Marketing|Marketing (?:Director|Manager)|CMO|Chief Marketing Officer)",
}
TITLE = "(?:" + "|".join(ROLE_PATTERNS.values()) + ")"
COMBINED_TITLE = rf"(?i:{TITLE}(?:\s*(?:&|and|/)\s*{TITLE})*)"
NAME = r"[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’.-]+(?:[ \t]+[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’.-]+){1,3}"
EMAIL = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
GENERAL = {
    "info",
    "hello",
    "contact",
    "office",
    "sales",
    "support",
    "admin",
    "team",
    "marketing",
    "service",
    "customerservice",
    "inquiries",
    "enquiries",
    "reception",
    "general",
}
PUBLIC_MAIL_HOSTS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "aol.com",
    "icloud.com",
}


def professional_url(value):
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value)
    return (
        value
        if parsed.scheme in {"http", "https"}
        and parsed.hostname in {"linkedin.com", "www.linkedin.com"}
        and parsed.path.startswith("/in/")
        else ""
    )


def category(title):
    for role, pattern in ROLE_PATTERNS.items():
        if any(
            re.fullmatch(pattern, part.strip(), re.IGNORECASE)
            for part in re.split(r"\s*(?:&|\band\b|/)\s*", title)
        ):
            return role
    return ""


def text_blocks(page):
    soup = BeautifulSoup(page.get("html", ""), "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    blocks = [
        tag.get_text(" ", strip=True)
        for tag in soup.select("article,li,p,section,div")
        if len(tag.get_text(" ", strip=True)) <= 550
    ]
    blocks += soup.get_text("\n", strip=True).splitlines()
    blocks += re.sub(
        r"\[([^\]]+)\]\([^)]+\)", r"\1", page.get("markdown", "")
    ).splitlines()
    return list(
        dict.fromkeys(re.sub(r"[*#_`]", "", b).strip() for b in blocks if b.strip())
    )


def extract(cid, root, pages, config):
    people = {}
    emails = {}
    signals = []
    all_text = []
    for page in pages:
        url = page["url"]
        date = page.get("timestamp", "")
        blocks = text_blocks(page)
        if domain(url) != root:
            continue
        all_text += blocks
        soup = BeautifulSoup(page.get("html", ""), "html.parser")
        candidates = []
        # Schema.org Person on official pages supplies exact paired fields.
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                obj = json.loads(script.string or script.get_text())
            except (ValueError, TypeError):
                continue

            def walk(value):
                if isinstance(value, dict):
                    if (
                        value.get("@type") == "Person"
                        and isinstance(value.get("name"), str)
                        and isinstance(value.get("jobTitle"), str)
                    ):
                        candidates.append(
                            (
                                value["name"],
                                value["jobTitle"],
                                value.get("url", ""),
                                value.get("sameAs", ""),
                                json.dumps(value),
                            )
                        )
                    for v in value.values():
                        walk(v)
                elif isinstance(value, list):
                    for v in value:
                        walk(v)

            walk(obj)
        # Explicit name/title pairs, including adjacent heading lines; never whole-site proximity.
        for b in blocks + [
            a + " — " + b
            for a, b in zip(blocks, blocks[1:])
            if len(a) < 85 and len(b) < 100
        ]:
            if re.search(
                r"\b(former|past|retired|late|in memory|previously|founded in|was our|served as)\b",
                b,
                re.IGNORECASE,
            ):
                continue
            for pattern in [
                rf"(?P<name>{NAME})\s*[,|:–—-]\s*(?P<title>{COMBINED_TITLE})(?!\w)",
                rf"(?P<title>{COMBINED_TITLE})\s*[,|:–—-]\s*(?P<name>{NAME})",
            ]:
                for m in re.finditer(pattern, b):
                    candidates.append((m["name"], m["title"], "", "", b))
        for name, title, bio, professional, context in candidates:
            role = category(title)
            if (
                not role
                or role not in config["decision_maker_roles"]
                or not re.fullmatch(NAME, name)
            ):
                continue
            if re.search(r"\b(former|retired|past|late)\b", context, re.IGNORECASE):
                continue
            if any(
                w in norm(name).split()
                for w in [
                    "our",
                    "team",
                    "contact",
                    "learn",
                    "meet",
                    "about",
                    "company",
                    "services",
                ]
            ):
                continue
            contact_id = stable_id("person", cid, norm(name))
            p = people.get(contact_id)
            if p:
                continue
            if isinstance(professional, list):
                professional = next(
                    (v for v in professional if professional_url(v)),
                    "",
                )
            professional = professional_url(professional)
            cards = [
                tag
                for tag in soup.select("article,li,p,section,div")
                if name in tag.get_text(" ", strip=True)
                and title in tag.get_text(" ", strip=True)
                and len(tag.get_text(" ", strip=True)) <= 550
            ]
            if cards:
                card = min(cards, key=lambda tag: len(tag.get_text(" ", strip=True)))
                professional = professional or next(
                    (
                        professional_url(a.get("href"))
                        for a in card.select("a[href]")
                        if professional_url(a.get("href"))
                    ),
                    "",
                )
                if not bio:
                    bio = next(
                        (
                            a.get("href")
                            for a in card.select("a[href]")
                            if name in a.get_text(" ", strip=True)
                        ),
                        "",
                    )
            associated_location = ""
            if context.startswith("{"):
                try:
                    person_data = json.loads(context)
                    work_location = (
                        person_data.get("workLocation")
                        or person_data.get("location")
                        or ""
                    )
                    associated_location = (
                        work_location.get("name", "")
                        if isinstance(work_location, dict)
                        else str(work_location)
                    )
                except ValueError:
                    pass
            bio = urljoin(url, bio) if isinstance(bio, str) and bio else url
            if domain(bio) != root:
                bio = url
            people[contact_id] = {
                "company_id": cid,
                "contact_id": contact_id,
                "name": name,
                "exact_title": title,
                "role_category": role,
                "location_id": "",
                "associated_location": associated_location,
                "current_role_source_url": url,
                "team_bio_url": bio,
                "professional_profile_url": professional,
                "public_work_email": "",
                "public_business_phone": next(
                    iter(
                        re.findall(
                            r"(?:\+1[- .]?)?\(?\d{3}\)?[- .]\d{3}[- .]\d{4}", context
                        )
                    ),
                    "",
                ),
                "confidence": "source_backed",
                "evidence_date": date,
                "evidence_excerpt": context[:550],
            }
        text = (
            "\n".join(blocks)
            + "\n"
            + page.get("html", "")
            + "\n"
            + page.get("markdown", "")
        )
        for email in EMAIL.findall(text):
            email = email.lower()
            mail_host = email.rsplit("@", 1)[1]
            # Free-hosted business addresses need explicit contact presentation on
            # the official page, not an unrelated footer or embedded script.
            free_public = mail_host in PUBLIC_MAIL_HOSTS and any(
                email in block.lower()
                and re.search(r"\b(contact|email|e-mail|office|reach)\b", block, re.I)
                for block in blocks
            )
            if (domain(mail_host) != root and not free_public) or email.rsplit(".", 1)[
                -1
            ] in [
                "png",
                "jpg",
                "svg",
                "webp",
                "js",
            ]:
                continue
            emails.setdefault(
                email,
                {
                    "email_id": stable_id("email", cid, email),
                    "company_id": cid,
                    "contact_id": "",
                    "email": email,
                    "status": "public_general_email",
                    "provenance": "public_source",
                    "deliverability": "unverified",
                    "source_url": url,
                    "pattern_evidence": [],
                    "evidence_date": date,
                },
            )
        for b in blocks:
            if (
                len(b) >= 15
                and len(b) <= 450
                and re.search(
                    r"\b(we |provid|specializ|offer|servic|manufactur|install|manag)",
                    b,
                    re.IGNORECASE,
                )
            ):
                signals.append({"text": b, "source_url": url})

    def patterns(name):
        parts = norm(name).split()
        first, last = parts[0], parts[-1]
        return {
            "first.last": first + "." + last,
            "flast": first[0] + last,
            "firstlast": first + last,
            "first": first,
            "first_last": first + "_" + last,
        }

    supported = {}
    for email, e in emails.items():
        local = email.split("@")[0]
        if local in GENERAL:
            continue
        matches = [p for p in people.values() if local in patterns(p["name"]).values()]
        if len(matches) == 1:
            p = matches[0]
            e.update(contact_id=p["contact_id"], status="public_named_email")
            p["public_work_email"] = email
            for pattern, value in patterns(p["name"]).items():
                if value == local and domain(email.rsplit("@", 1)[1]) == root:
                    supported.setdefault(pattern, []).append(
                        {
                            "email": email,
                            "name": p["name"],
                            "source_url": e["source_url"],
                        }
                    )
    # Require two independently named public examples and a single supported pattern.
    valid = [p for p, ex in supported.items() if len({x["name"] for x in ex}) >= 2]
    if len(valid) == 1:
        pattern = valid[0]
        for p in people.values():
            if p["public_work_email"]:
                continue
            email = patterns(p["name"])[pattern] + "@" + root
            if email in emails:
                continue
            emails[email] = {
                "email_id": stable_id("email", cid, email),
                "company_id": cid,
                "contact_id": p["contact_id"],
                "email": email,
                "status": "inferred_pattern",
                "provenance": "inferred_pattern",
                "deliverability": "unverified",
                "source_url": p["current_role_source_url"],
                "pattern_evidence": supported[pattern],
                "evidence_date": p["evidence_date"],
            }
    contacts = sorted(
        people.values(),
        key=lambda p: (
            config["decision_maker_roles"].index(p["role_category"]),
            p["name"],
        ),
    )[: config["max_contacts_per_company"]]
    chosen = {p["contact_id"] for p in contacts}
    contact_priority = {p["contact_id"]: index for index, p in enumerate(contacts)}
    candidates = sorted(
        [
            e
            for e in emails.values()
            if not e["contact_id"] or e["contact_id"] in chosen
        ],
        key=lambda e: (
            {"public_named_email": 0, "public_general_email": 1, "inferred_pattern": 2}[
                e["status"]
            ],
            contact_priority.get(e["contact_id"], len(contacts)),
            e["email"],
        ),
    )[: config["max_emails_per_company"]]
    exported_emails = {e["email"] for e in candidates}
    for person in contacts:
        if person["public_work_email"] not in exported_emails:
            person["public_work_email"] = ""
    if not candidates:
        candidates = [
            {
                "email_id": stable_id("email", cid, "not_found"),
                "company_id": cid,
                "contact_id": "",
                "email": "",
                "status": "not_found",
                "provenance": "none",
                "deliverability": "unverified",
                "source_url": "",
                "evidence_date": max(
                    [p.get("timestamp", "") for p in pages], default=""
                ),
            }
        ]
    terms = [
        w.rstrip("s")
        for w in norm(config["business_category"]).split()
        if w not in {"company", "companies", "service", "services", "the", "and", "of"}
    ]
    confirmations = [
        s for s in signals if terms and all(t in norm(s["text"]) for t in terms)
    ]
    service_text = " ".join(all_text).lower()
    mix = [
        label
        for label, pattern in {
            "single_family": r"\b(single.family|sfr)\b",
            "multifamily": r"\b(multi.family|apartments)\b",
            "hoa": r"\b(hoa|homeowner association)\b",
            "commercial": r"\bcommercial\b",
            "residential": r"\bresidential\b",
        }.items()
        if re.search(pattern, service_text)
    ]
    return {
        "contacts": contacts,
        "emails": candidates,
        "summary": signals[0]["text"] if signals else "",
        "service_mix": mix,
        "service_evidence": signals[:12],
        "source_relevance": "confirmed" if confirmations else "needs_review",
        "relevance_evidence": confirmations[:3],
        "pages": pages,
    }
