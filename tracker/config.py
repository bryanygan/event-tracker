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
SETLISTFM_API_KEY = os.getenv("SETLISTFM_API_KEY", "").strip()  # "first Philly show since ..." history
# The user's listening history (ttspotslideshow's plays.db), read-only, for the niche boost. Personal data:
# never copied into the repo; it only affects scores and dashboard badges.
LISTENING_DB = os.getenv("LISTENING_DB", "").strip()

# Cloudflare Pages read-only snapshot (tracker/publish.py). Leave PAGES_PROJECT empty to disable.
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
PAGES_PROJECT = os.getenv("PAGES_PROJECT", "").strip()
PAGES_ALLOW_PUBLIC = os.getenv("PAGES_ALLOW_PUBLIC", "") == "1"  # skip the Access check (not recommended)

# Web context on idea cards (tracker/context.py). Leave empty to turn lookups off.
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "").strip()
CONTEXT_CACHE_DAYS = 7      # reuse one lookup per artist this long
CONTEXT_DAILY_CAP = 10      # all lookups per ET day, failures included (~$0.0016 each)
CONTEXT_AUTO_PER_DAY = 5    # of which the pipeline may use this many, on "strong" ideas only

# 9:16 slides (tracker/slides.py): the small line at the top of every slide. Editable per deck too.
SLIDES_BRAND = os.getenv("SLIDES_BRAND", "PHILLY EVENTS").strip()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# Drafts are written by Claude when ANTHROPIC_API_KEY is set (DRAFT_PROVIDER=ollama forces the local model).
# Ollama stays the fallback: over the daily cap, or when the API call fails.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
DRAFT_PROVIDER = os.getenv("DRAFT_PROVIDER", "").strip().lower() or ("claude" if ANTHROPIC_API_KEY else "ollama")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5").strip()
CLAUDE_DAILY_CAP = int(os.getenv("CLAUDE_DAILY_CAP", "40"))  # model calls per ET day (~$0.0005 each on Haiku)
# USD per million tokens (input, output), for the cost shown on the Runs tab. Haiku's is for prompts under 100K.
CLAUDE_PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00)}
# Bind to localhost only: the dashboard is for you, not the network.
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))  # Homebridge UI uses 8581

# One pipeline run at a time across processes (tracker/run.py). The 11:30 midday check (--midday) only looks up
# this many artists' Philly history on setlist.fm, newest first, so new tours still get "back after X years".
RUN_LOCK = DATA_DIR / "pipeline.lock"
MIDDAY_SETLIST_ARTISTS = 15

# Editing from your phone (tracker/remote.py): a Cloudflare Tunnel hostname that points at the dashboard, locked
# by a Cloudflare Access app. Requests for REMOTE_HOST are only served with a valid Access login for that app.
# All three empty = phone editing off (the default). Set them with `python -m tracker.remote --setup HOST`.
REMOTE_HOST = os.getenv("REMOTE_HOST", "").strip().lower()              # e.g. events.example.com
CF_ACCESS_TEAM_DOMAIN = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip().rstrip("/")  # https://<team>.cloudflareaccess.com
CF_ACCESS_AUD = os.getenv("CF_ACCESS_AUD", "").strip()                  # the Access app's "Application Audience" tag
# Optional extra check on top of the Access policy: only these emails (comma-separated) may use it.
REMOTE_EMAILS = {e.strip().lower() for e in os.getenv("REMOTE_EMAILS", "").split(",") if e.strip()}

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
COMEBACK_MIN_YEARS = 3             # "first Philly show since <year>" when the gap is at least this
MULTI_ACT_MIN = 5                  # acts on one bill for the last show to count as a festival/radio show
COMEBACK_BONUS = 10                # score bump for comebacks (the brief's 'back after X years' angle)
PICKS_MIN, PICKS_MAX = 3, 5        # "my N picks" cut of a weekend / stacked-month roundup (best by notability)
LANE_WEEKEND_MIN = 3               # shows in your lane needed for a "your lane this weekend" cut
LANE_WEEKEND_MAX_OVERLAP = 0.8     # skip the lane cut when this share of its shows is already in another roundup

# --- Genres (tracker/genres.py) ---
# Ticketmaster labels that say nothing about the music. Never used as a genre, never drive a genre roundup.
NON_GENRES = {"Undefined", "Other", "Miscellaneous", "Music"}
# Venue-calendar acts have no genre; look them up as Ticketmaster attractions (exact name, unambiguous only).
GENRE_LOOKUPS_PER_RUN = 40         # TM calls per run (a run itself uses ~4); unfilled acts wait for the next run
GENRE_RECHECK_DAYS = 30            # retry "not found" / ambiguous names after this long
# Calendar "headliners" that are really event names: never look these up (a namesake would match).
GENRE_LOOKUP_SKIP_PATTERNS = [r"\bopen mic\b", r"\bkaraoke\b", r"\btrivia\b", r"\bnight\b", r"\bparty\b",
                              r"\bshowcase\b", r"\bjam\b", r"\bbingo\b", r"\bbrunch\b", r"\bcomedy\b", r"\bdj set\b",
                              r"\bdance party\b", r"\bfestival\b", r"\bfest\b", r"\bpresents\b", r"\bmarket\b"]

# --- Content queue (tracker/opportunities.py) ---
# EXPERIMENTAL weights for "is this a good video right now?", separate from event notability (scoring.py).
# Guesses from the creator roadmap, not measured: revisit once logged posts have numbers. Bump the version
# whenever you change a weight or a rule, so a post's saved score says which rules produced it.
OPPORTUNITY_RULES_VERSION = "2026-10-22.AB"  # .A: lane weekend cut story value; .B: trend notes fill "search"
OPPORTUNITY_WEIGHTS = {
    "audience": 0.25,    # how many people care (notability, incl. your-taste boost)
    "timing": 0.25,      # fresh news / deadline coming up
    "story": 0.20,       # comeback, big name in a small room, a genuinely packed month
    "local": 0.15,       # Philly + in your lane
    "search": 0.10,      # search demand / trends: from your trend notes (trends.py); unknown when none match
    "ready": 0.05,       # facts complete, draft written and clean
}
NEWS_FRESH_HOURS = 24      # announcements: post within a day...
NEWS_EXPIRES_DAYS = 7      # ...and after a week it's not news anymore
RECENT_POST_DAYS = 14      # warn when you already posted about the same artist this recently
# Opportunity score bands on the cards. Set from the first real queue (2026-10-21): at 70/50, 14 of 21 ideas
# were "strong" because every presale alert is by construction <72h out and maxes the timing component.
QUEUE_STRONG, QUEUE_MEDIUM = 80, 65
REASON_MIN = 75            # a component counts as a reason on the card at this value or above
TREND_NOTES_MAX = 500      # refuse new notes past this (each is matched against every idea on every queue load)
TREND_NOTE_DAYS = 7       # a trend note stops counting this long after you saw it (unless you set expires_at)

# --- Posted videos (tracker/posts.py) ---
# A group (a format, a video style) needs this many posts with numbers at the same age before the
# dashboard compares it with others. Below that it's shown as "too few to compare".
ANALYTICS_MIN_POSTS = 3

# --- Your niche (tracker/listening.py) ---
# The user covers mostly rap, hyperpop, pop, R&B, EDM, bedroom/indie pop, but is open to anything notable.
# Plays are counted over the last LISTENING_WINDOW_DAYS. Bonuses add to the normal 0-100 score.
LISTENING_WINDOW_DAYS = 730
ROTATION_MIN_PLAYS = 25            # "in your rotation": new shows/presales alert even below the score bars
PLAYS_BONUS = [(200, 20), (50, 12), (10, 6)]  # (min plays, bonus), first match wins
NICHE_GENRE_BONUS = 5
# Genre buckets from ttspotslideshow's artist_genres table that count as your lane.
NICHE_BUCKETS = {"hip-hop", "trap", "rage", "plugg", "melodic-rap", "drill", "pop", "electronic", "r&b", "indie"}
# Ticketmaster genres / subgenres that count as your lane.
NICHE_TM_GENRES = {"Hip-Hop/Rap", "R&B", "Dance/Electronic", "Pop"}
NICHE_TM_SUBGENRES = {"Indie Pop", "Hyperpop", "Bedroom Pop", "Alternative Rap", "Trap", "Urban"}

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
    "warehouse on watts": 35,
    "underground arts": 30,
    "miller theater": 30,
    # small rooms
    "the foundry": 25,                 # small room inside the Fillmore
    "world cafe live": 25,
    "noto": 25,
    "perelman theater": 25,
    "johnny brenda": 20,
    "kung fu necktie": 15,
    "philamoca": 15,
    "milkboy": 15,
    "nikki lopez": 15,
    "hard rock cafe": 15,
}
DEFAULT_VENUE_TIER = 20

# --- Venue calendars (tracker/fetchers/venue_calendars.py) ---
# Rooms that mostly don't sell through Ticketmaster but publish schema.org Event data on their own sites.
# `name` should match how Ticketmaster names the venue, so duplicates collapse and VENUE_TIERS applies.
VENUE_CALENDARS = [
    {"key": "underground-arts", "name": "Underground Arts", "url": "https://undergroundarts.org/", "city": "Philadelphia"},
    {"key": "milkboy", "name": "MilkBoy Philadelphia", "url": "https://milkboyphilly.com/", "city": "Philadelphia"},
    {"key": "ardmore-music-hall", "name": "Ardmore Music Hall", "url": "https://ardmoremusichall.com/", "city": "Ardmore"},
    {"key": "warehouse-on-watts", "name": "Warehouse on Watts", "url": "https://www.wowphilly.com/", "city": "Philadelphia"},
    {"key": "the-ave-live", "name": "The Ave Live", "url": "https://theavelive.com/", "city": "Philadelphia"},
    # Etix venues: no JSON-LD, so a per-site HTML parser ("parser"). See venue_calendars.PARSERS.
    {"key": "johnny-brendas", "name": "Johnny Brenda's", "url": "https://johnnybrendas.com/", "city": "Philadelphia",
     "parser": "rhp"},
    {"key": "kung-fu-necktie", "name": "Kung Fu Necktie", "url": "https://kungfunecktie.com/", "city": "Philadelphia",
     "parser": "rhp"},
    {"key": "philamoca", "name": "PhilaMOCA", "url": "https://www.philamoca.org/", "city": "Philadelphia",
     "parser": "philamoca"},
]
# Calendar entries that aren't concerts (matched against the cleaned show name).
VENUE_JUNK_PATTERNS = [
    r"\bclosed\b", r"\bprivate event\b", r"\bbrunch\b", r"\bkids?\b", r"\bchildren'?s\b", r"\btrivia\b",
    r"\bkaraoke\b", r"\bbingo\b", r"\bcomedy\b", r"\bstand-?up\b", r"\bbook signing\b", r"\bflea\b",
    r"\bmarket\b", r"\bart show\b", r"\bworkshop\b", r"\bopen mic\b", r"\bwatch party\b", r"\bpancakes\b",
    # PhilaMOCA also hosts film nights and wrestling
    r"\bscreenings?\b", r"\bpremiere\b", r"\bfilms?\b", r"\bdocumentary\b", r"\bmovies?\b", r"\bwrestling\b",
    r"\bin attendance\b",  # "TROLL 2 w/ George Hardy in attendance!" = a screening with a guest
]

# Ticketmaster lists add-ons as "events". Names matching these are skipped.
JUNK_EVENT_PATTERNS = [
    r"\bparking\b", r"\bshuttle\b", r"\bupgrade\b", r"\bvip (package|experience)\b",
    r"\bhospitality\b", r"\bsuite\b", r"\blot [a-z0-9]\b", r"\bgift card\b",
]
