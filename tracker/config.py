"""Central settings. Secrets come from .env; everything else is tunable here."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("TRACKER_DB", DATA_DIR / "tracker.db"))
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = ROOT / "logs"

# The Discovery API only uses the consumer key (sent as `apikey`). The secret is kept in .env
# for possible future OAuth-style endpoints but isn't used today.
TICKETMASTER_API_KEY = (os.getenv("TICKETMASTER_CONSUMER_KEY") or os.getenv("TICKETMASTER_API_KEY", "")).strip()

# Cloudflare Pages read-only snapshot (tracker/publish.py). Leave PAGES_PROJECT empty to disable.
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
PAGES_PROJECT = os.getenv("PAGES_PROJECT", "").strip()
PAGES_ALLOW_PUBLIC = os.getenv("PAGES_ALLOW_PUBLIC", "") == "1"  # skip the Access check (not recommended)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
# Bind to localhost only: the dashboard is for you, not the network.
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))  # Homebridge UI uses 8581

# --- Search area: City Hall, ~30 mi radius (covers Camden, KoP, Wilmington-ish) ---
LATLONG = "39.9526,-75.1652"
RADIUS_MILES = 30
LOOKAHEAD_DAYS = 240  # how far ahead to fetch events
TIMEZONE = "America/New_York"

# --- Ticketmaster API etiquette: 5 req/sec and 5000/day on the free tier ---
TM_MIN_SECONDS_BETWEEN_CALLS = 0.3
TM_DAILY_CALL_BUDGET = 1000  # our own cap, well under the 5000 limit

# --- Detector tuning ---
PRESALE_WINDOW_HOURS = 72          # alert when presale/on-sale opens within this window
PRESALE_MIN_SCORE = 45             # skip presale alerts for small acts
STACKED_MONTH_MIN_EVENTS = 5       # notable shows in one genre/month to call it "stacked"
STACKED_MONTH_MIN_SCORE = 50       # an event must score at least this to count as notable
WEEKEND_TOP_N = 8                  # events per weekend roundup
NEW_TOUR_MIN_SCORE = 35            # ignore brand-new listings below this notability

# --- Sports ---
# Extra Ticketmaster classifications fetched each run (~1 API call each), and which teams to keep from them.
# The basketball feed also has Villanova, the Delaware Blue Coats and NCAA events; to track one, add its
# Ticketmaster attraction name here. "short" is what scripts say; "aliases" are accepted in hooks.
SPORTS_CLASSIFICATIONS = ["basketball"]
SPORTS_TEAMS = {
    "Philadelphia 76ers": {"short": "Sixers", "aliases": ["Sixers", "76ers"]},
}
# Opponents that make a home game a "big game" alert (Ticketmaster attraction names).
MARQUEE_OPPONENTS = [
    "Boston Celtics", "New York Knicks", "Los Angeles Lakers", "Golden State Warriors", "Brooklyn Nets",
    "Milwaukee Bucks", "Miami Heat", "Cleveland Cavaliers", "Denver Nuggets", "Oklahoma City Thunder",
]
SPORTS_BASE_SCORE = 55        # any home game; music scores come from popularity/venue/tour instead
MARQUEE_BONUS = 25
WEEKEND_GAME_BONUS = 5        # Fri-Sun games
BIG_GAME_WINDOW_DAYS = 7      # raise a "big game" alert this many days ahead

# Sale windows that aren't really presales (add-on packages). Matched case-insensitively.
IGNORED_SALE_PATTERNS = [r"\bvip\b", r"\bpackage", r"\bhospitality\b", r"\bsuite", r"\bparking\b"]

# Bigger room = bigger draw: one of the three notability signals (see scoring.py).
# Matched case-insensitively as substrings of the TM venue name; FIRST match wins,
# so more specific names go first (e.g. Mann's main stage before its smaller Skyline Stage).
# Numbers are rough capacity tiers, not exact.
VENUE_TIERS = {
    # stadiums
    "lincoln financial field": 100,
    "citizens bank park": 100,
    "subaru park": 85,
    # arenas / amphitheaters
    "xfinity mobile arena": 90,
    "wells fargo center": 90,          # old name of Xfinity Mobile Arena
    "freedom mortgage pavilion": 80,
    "td pavilion": 80,                 # Mann main stage
    "mann": 65,                        # Skyline Stage at Highmark Mann, etc.
    "liacouras center": 70,
    "cure insurance arena": 70,
    # big theaters / large clubs (2,000-3,500)
    "the met presented": 65,
    "the met philadelphia": 65,
    "the fillmore": 60,
    "franklin music hall": 55,
    "xcite center": 55,
    "pennsylvania convention center": 55,
    "marian anderson hall": 50,
    "academy of music": 50,
    "the dell music center": 50,
    "union transfer": 50,
    # mid-size (800-1,500)
    "theatre of living arts": 45,
    "brooklyn bowl": 45,
    "keswick theatre": 45,
    "the queen": 40,
    "lansdowne theater": 35,
    "ardmore music hall": 35,
    "scottish rite auditorium": 35,
    "harrah's": 35,
    "colonial theatre": 30,
    "the playhouse on rodney square": 30,
    "the ave live": 30,
    "underground arts": 30,
    "miller theater": 30,
    # small rooms
    "the foundry": 25,                 # small room inside the Fillmore
    "world cafe live": 25,
    "noto": 25,
    "perelman theater": 25,
    "johnny brenda": 20,
    "milkboy": 15,
    "nikki lopez": 15,
    "hard rock cafe": 15,
}
DEFAULT_VENUE_TIER = 20

# Ticketmaster lists add-ons as "events". Names matching these are skipped.
JUNK_EVENT_PATTERNS = [
    r"\bparking\b", r"\bshuttle\b", r"\bupgrade\b", r"\bvip (package|experience)\b",
    r"\bhospitality\b", r"\bsuite\b", r"\blot [a-z0-9]\b", r"\bgift card\b",
]
