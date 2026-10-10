"""Atomic persistence, credential redaction and conservative identity primitives."""

import csv
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import phonenumbers
import tldextract

# Bundled PSL only: no first-use download, no cache writes, includes tenant suffixes.
_psl = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)
SHARED_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "google.com",
    "yelp.com",
    "yellowpages.com",
    "gmail.com",
    "outlook.com",
    "yahoo.com",
    "wixsite.com",
    "wordpress.com",
    "business.site",
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(kind, *parts):
    return (
        kind
        + "_"
        + hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]
    )


def norm(value):
    return re.sub(
        r"[^a-z0-9]+", " ", str(value or "").casefold().replace("&", " and ")
    ).strip()


def domain(value):
    text = str(value or "").strip()
    p = urlparse(text if "://" in text else "https://" + text)
    host = (p.hostname or "").lower().rstrip(".")
    if p.scheme not in {"http", "https"} or p.username or p.password or not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    result = _psl(host)
    root = result.top_domain_under_public_suffix
    return root if result.suffix and root not in SHARED_DOMAINS else ""


def phone(value, country="US"):
    try:
        p = phonenumbers.parse(str(value or ""), country.upper())
        return (
            phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
            if phonenumbers.is_possible_number(p)
            else ""
        )
    except phonenumbers.NumberParseException:
        return ""


def identity_keys(row):
    keys = []
    for kind, value in [
        ("place", row.get("place_id")),
        ("domain", domain(row.get("root_domain") or row.get("website"))),
        ("phone", phone(row.get("phone"), row.get("country") or "US")),
    ]:
        if value:
            keys.append((kind, str(value)))
    if norm(row.get("company_name")) and norm(row.get("address")):
        keys.append(
            ("name_address", norm(row["company_name"]) + "|" + norm(row["address"]))
        )
    return keys


def redact(value, _secrets=None):
    if _secrets is None:
        _secrets = tuple(
            v
            for k, v in os.environ.items()
            if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k) and len(v) >= 6
        )
    if isinstance(value, dict):
        sensitive_fields = {
            "key",
            "apikey",
            "rapidapikey",
            "firecrawlkey",
            "firecrawlapikey",
            "authorization",
            "token",
            "accesstoken",
            "refreshtoken",
            "secret",
            "clientsecret",
            "password",
            "cookie",
            "setcookie",
        }
        return {
            str(k): "[REDACTED]"
            if (
                re.sub(r"[^a-z0-9]", "", str(k).lower()) in sensitive_fields
                or re.sub(r"[^a-z0-9]", "", str(k).lower()).endswith("apikey")
            )
            else redact(v, _secrets)
            for k, v in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [redact(v, _secrets) for v in value]
    if isinstance(value, str):
        for secret in _secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(Bearer\s+)[\w.\-]+", r"\1[REDACTED]", value)
        return re.sub(
            r'(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s&;,"\'}]+',
            r"\1[REDACTED]",
            value,
        )
    return value


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, value):
    atomic_text(path, json.dumps(redact(value), indent=2, ensure_ascii=False) + "\n")


def write_csv(path, rows, fields):
    import io

    f = io.StringIO(newline="")
    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for row in rows:
        safe = {
            k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
            for k, v in redact(row).items()
        }
        # Prevent spreadsheet formula execution in exported untrusted cells.
        safe = {
            k: "'" + v
            if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r"))
            else v
            for k, v in safe.items()
        }
        w.writerow(safe)
    atomic_text(path, f.getvalue())


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Resource busy; another canonical process owns " + str(path)
            ) from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
