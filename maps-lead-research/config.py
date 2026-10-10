"""Shared configuration for all scraper layers."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- RapidAPI ---
# Sign up at rapidapi.com and subscribe to "Maps Data" by alexanderxbx.
# Set RAPIDAPI_KEY in .env (see .env.example).
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "maps-data.p.rapidapi.com"
RAPIDAPI_ENDPOINT = "https://maps-data.p.rapidapi.com/searchmaps.php"

# --- Search config ---
KEYWORDS = [
    "med spa",
    "medical spa",
    "aesthetic clinic",
    "aesthetics clinic",
    "botox clinic",
    "dermal fillers",
    "cosmetic injector",
    "laser skin clinic",
    "skin rejuvenation clinic",
    "aesthetic medicine",
]

# Swap this path to target a different metro / state / national set.
ZIP_CSV_PATH = "zips/aesthetics_test_10.csv"

# --- Database ---
DB_PATH = "leads.db"

# --- Concurrency ---
MAPS_THREADS = 10
EMAIL_THREADS = 20

# --- HTTP ---
REQUEST_TIMEOUT = 5
MAX_RETRIES = 2
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# --- Email classification ---
# Used by Layer 2 to split business-domain emails into "primary" (likely
# decision-maker: jane@example.com, sam@example.com) vs "fallback" (generic
# shared inbox: info@x.com, hello@x.com).
PERSONAL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "aol.com",
    "godaddy.com",
}

# Ordered — first match wins when picking the fallback email.
GENERIC_EMAIL_PREFIXES = [
    "info",
    "hello",
    "contact",
    "support",
    "admin",
    "office",
    "appointments",
    "booking",
]

# Placeholder / template / noreply local parts that routinely leak from stock
# website themes (demo@demo.com, yourname@example.com, noreply@...).
FAKE_EMAIL_LOCAL_BLOCKLIST = {
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
}

# Domains that never belong to a real med spa lead: template placeholders,
# and PR / marketing agencies whose contact emails get embedded in press
# releases on client sites.
FAKE_EMAIL_DOMAIN_BLOCKLIST = {
    "example.com",
    "yourcompany.com",
    "yoursite.com",
    "domain.com",
    "email.com",
    "yourdomain.com",
    "placeholder.com",
    "5wpr.com",
    "ekcpr.com",
    "qodeinteractive.com",
    "redspotinteractive.com",
}

# --- Pagination ---
# RapidAPI Maps Data returns up to ~20 results per call. Keep pulling until
# a page returns empty or we hit this cap.
MAX_PAGES_PER_QUERY = 10
