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

TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY", "").strip()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

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
STACKED_MONTH_MIN_EVENTS = 5       # notable shows in one genre/month to call it "stacked"
STACKED_MONTH_MIN_SCORE = 40       # an event must score at least this to count as notable
WEEKEND_TOP_N = 8                  # events per weekend roundup
NEW_TOUR_MIN_SCORE = 30            # ignore brand-new listings below this notability

# Bigger room = bigger draw. Used for notability when Spotify data is missing.
# Matched case-insensitively as substrings of the TM venue name.
VENUE_TIERS = {
    "lincoln financial field": 100,
    "citizens bank park": 100,
    "wells fargo center": 90,
    "xfinity mobile arena": 90,
    "freedom mortgage pavilion": 80,
    "mann center": 75,
    "liacouras center": 70,
    "the met philadelphia": 65,
    "the fillmore": 60,
    "franklin music hall": 55,
    "union transfer": 50,
    "the dell music center": 50,
    "keswick theatre": 45,
    "academy of music": 45,
    "the queen": 40,
    "underground arts": 30,
    "johnny brenda": 25,
    "world cafe live": 25,
}
DEFAULT_VENUE_TIER = 25

# Ticketmaster lists add-ons as "events". Names matching these are skipped.
JUNK_EVENT_PATTERNS = [
    r"\bparking\b", r"\bshuttle\b", r"\bupgrade\b", r"\bvip (package|experience)\b",
    r"\bhospitality\b", r"\bsuite\b", r"\blot [a-z0-9]\b", r"\bgift card\b",
]
