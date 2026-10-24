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
LAST_FM_API_KEY = os.getenv("LAST_FM_API_KEY", "").strip()  # read-only calls (tracker/discovery.py, M7)
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
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium").strip()  # low | medium | high; medium cost the same as low in testing
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
# Deezer's genre for an artist: the most common genre across their releases, in Ticketmaster's words.
# Ticketmaster's own label is sometimes plainly wrong (a hyperpop act as Rock, a rock band as Dance/Electronic).
DEEZER_GENRES = {
    "Pop": "Pop", "Rap/Hip Hop": "Hip-Hop/Rap", "R&B": "R&B", "Soul & Funk": "R&B", "Dance": "Dance/Electronic",
    "Electro": "Dance/Electronic", "Rock": "Rock", "Alternative": "Alternative", "Metal": "Metal",
    "Country": "Country", "Folk": "Folk", "Jazz": "Jazz", "Blues": "Blues", "Reggae": "Reggae",
    "Reggaeton": "Latin", "Latin Music": "Latin", "Salsa": "Latin", "Cumbia": "Latin",
    "Traditional Mexicano": "Latin", "Brazilian Music": "Latin", "Classical": "Classical", "Christian": "Religious",
}
DEEZER_GENRE_MIN_RELEASES = 3      # releases with a usable genre before Deezer's genre counts
DEEZER_GENRE_MIN_SHARE = 0.6       # ...and this share of them must agree
# Deezer's genre replaces Ticketmaster's only across these families: Pop vs Hip-Hop/Rap for a crossover act is
# a judgment call (Ticketmaster's stays), Rock vs Pop is a wrong label. A genre in no family is never replaced.
GENRE_FAMILIES = {
    "Pop": "pop", "Hip-Hop/Rap": "pop", "R&B": "pop", "Dance/Electronic": "pop",
    "Rock": "rock", "Alternative": "rock", "Metal": "rock",
    "Country": "country", "Folk": "country", "Jazz": "jazz", "Blues": "jazz", "Latin": "latin",
    "Reggae": "reggae", "Classical": "classical", "Religious": "religious",
}
# Deezer genres too broad to overrule Ticketmaster (they still fill a show with no genre). Deezer files indie pop
# (bedroom-pop acts Ticketmaster calls Pop) under Alternative; checked on the live data 2026-10-22.
DEEZER_FILL_ONLY = {"Alternative"}
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
# Music stories (tracker/music.py, roadmap M5): their own score, since a national story has no Philly date to judge.
# From your M0 judgments (2026-10-23): the angle decided a yes, not how much you play the artist, so content potential
# carries the most weight and your artists a modest share. Momentum is unknown until M7 and left out, like search.
MUSIC_RULES_VERSION = "2026-10-23.M7"
MUSIC_WEIGHTS = {
    "content": 0.30,     # the kind of story and its hook (brand tie-in, tour, roundup over chart notes)
    "urgency": 0.20,     # fresh news, or a release date coming up
    "relevance": 0.15,   # watchlist tier, your plays, your feedback buttons
    "confidence": 0.15,  # catalog-confirmed > two outlets > one source; rumors and conflicts score low
    "search": 0.10,      # your trend notes (trends.py)
    "momentum": 0.10,    # M7: unknown until then
}
MUSIC_POLITICS_CAP = 60    # politics: a heavy penalty, not a block (a meme angle won once); Interested lifts it
MUSIC_CONFLICT_CAP = 64    # sources disagree on a date: never "strong" until it's sorted out
# Automatic Send to queue for stories that clear both bars. Off until M9: while Music news is its own tab, you
# decide what reaches What to post. Sensitive stories are never sent automatically.
MUSIC_AUTO_SEND = False
MUSIC_AUTO_SEND_MIN = 80
# Music video ideas (tracker/music_formats.py, roadmap M6). music_story comes from Send to queue; the roundups are
# built from the release catalogs by the news job (Thursday to Sunday for New Music Friday, from the 15th for next
# month's albums), when there are at least MUSIC_ROUNDUP_MIN releases to list.
MUSIC_KINDS = ("music_story", "new_music_friday", "upcoming_albums", "emerging_artists")
MUSIC_ROUNDUP_MIN = 3
MUSIC_ROUNDUP_MAX = 8      # releases listed, one slide each (slides.MAX_SHOWS), best first
MUSIC_ROUNDUP_PER_ARTIST = 2
# Artist discovery and momentum (tracker/discovery.py, roadmap M7). Experimental, labelled as such on the dashboard.
DISCOVERY_REFRESH_DAYS = 7         # similar artists and tag lists: rebuilt weekly from a week-long cache
DISCOVERY_SEEDS = 25               # your most-played watchlist artists whose Last.fm similar artists are read
DISCOVERY_SIMILAR_PER_SEED = 20
DISCOVERY_TAGS = ["hyperpop", "underground rap", "plugg", "rage", "pluggnb", "digicore", "alternative rnb",
                  "bedroom pop", "indie pop", "edm"]
DISCOVERY_TAG_ARTISTS = 50
DISCOVERY_MAX_CANDIDATES = 150     # the best-evidenced candidates are kept and snapshotted daily
METRICS_ARTISTS_PER_CHECK = 15     # daily snapshots, a slice per 30-minute news check (~3 calls each)
# Momentum: growth against the artist's own baseline, never audience size. A signal counts only with at least
# MOMENTUM_MIN_OBS observations spanning MOMENTUM_MIN_DAYS, from a base of at least MOMENTUM_MIN_BASE.
MOMENTUM_MIN_OBS = 3
MOMENTUM_MIN_DAYS = 7
MOMENTUM_MIN_BASE = {("deezer", "fans"): 1000, ("lastfm", "listeners"): 2000, ("listenbrainz", "listens"): 500}
MOMENTUM_GROWTH_PER_WEEK = 0.03    # +3% a week counts as growing
MOMENTUM_MIN_MENTIONS = 2          # outlets in the last 7 days, and more than the 7 days before
NEWS_FRESH_HOURS = 24      # announcements: post within a day...
NEWS_EXPIRES_DAYS = 7      # ...and after a week it's not news anymore
RECENT_POST_DAYS = 14      # warn when you already posted about the same artist this recently
# Opportunity score bands on the cards. Set from the first real queue (2026-10-21): at 70/50, 14 of 21 ideas
# were "strong" because every presale alert is by construction <72h out and maxes the timing component.
QUEUE_STRONG, QUEUE_MEDIUM = 80, 65
REASON_MIN = 75            # a component counts as a reason on the card at this value or above
TREND_NOTES_MAX = 500      # refuse new notes past this (each is matched against every idea on every queue load)
TREND_NOTE_DAYS = 7       # a trend note stops counting this long after you saw it (unless you set expires_at)

# --- Editorial calendar (tracker/planner.py) ---
# A nice-to-have tab. A plan is yours: a posting time and a series label on an idea, a shortlist for that day
# rather than a commitment (plan several, pick on the day). Nothing posts at that time.
CALENDAR_DAYS = 14                 # this week and next, starting Monday
CALENDAR_DEFAULT_HOUR = 18         # "Plan" on a day without a time picks 6 PM local
# Default series by format: recurring shows of the page. Change a single idea's series on its card.
SERIES_FOR_FORMAT = {
    "weekend": "Philly this weekend", "stacked_month": "Stacked month", "presale": "Presale alarm",
    "new_tour": "Just announced", "status_change": "Show update", "team_week": "Game week", "big_game": "Game week",
    "experience": "Philly happenings", "things_to_do": "Things to do this weekend",
    "music_story": "Music news", "new_music_friday": "New Music Friday", "upcoming_albums": "Upcoming albums",
    "emerging_artists": "Artists to watch",
}
# How often a series usually runs. The Series table marks one "behind" when its last post is older than this.
SERIES_CADENCE_DAYS = {"Philly this weekend": 7, "Stacked month": 30}

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
# Home games of these teams, from Ticketmaster's feed for each team's `classification` (~1 API call each per
# run). The feeds also carry college, G League and minor-league games; only teams listed here are kept (to track
# one, add its Ticketmaster attraction name). "short" is what scripts say; "aliases" are accepted in hooks;
# "league" and "emoji" go in hashtags and captions. "marquee": opponents that make a home game a "big game" alert
# (Ticketmaster attraction names). "every_game_big": every home game is its own alert (a team with one home game
# a week), and no "home games this week" roundup.
SPORTS_TEAMS = {
    "Philadelphia 76ers": {"short": "Sixers", "aliases": ["Sixers", "76ers"], "classification": "basketball",
                           "league": "NBA", "emoji": "🏀", "marquee": [
        "Boston Celtics", "New York Knicks", "Los Angeles Lakers", "Golden State Warriors", "Brooklyn Nets",
        "Milwaukee Bucks", "Miami Heat", "Cleveland Cavaliers", "Denver Nuggets", "Oklahoma City Thunder"]},
    "Philadelphia Flyers": {"short": "Flyers", "aliases": ["Flyers"], "classification": "hockey",
                            "league": "NHL", "emoji": "🏒", "marquee": [
        "Pittsburgh Penguins", "New York Rangers", "New Jersey Devils", "Washington Capitals", "Boston Bruins",
        "New York Islanders"]},
    "Philadelphia Eagles": {"short": "Eagles", "aliases": ["Eagles", "Birds"], "classification": "football",
                            "league": "NFL", "emoji": "🦅", "every_game_big": True, "marquee": [
        "Dallas Cowboys", "New York Giants", "Washington Commanders", "Kansas City Chiefs", "San Francisco 49ers"]},
    "Philadelphia Phillies": {"short": "Phillies", "aliases": ["Phillies", "Phils"], "classification": "baseball",
                              "league": "MLB", "emoji": "⚾", "marquee": [
        "New York Mets", "Atlanta Braves", "Los Angeles Dodgers", "New York Yankees", "Washington Nationals"]},
    "Philadelphia Union": {"short": "Union", "aliases": ["Union"], "classification": "soccer",
                           "league": "MLS", "emoji": "⚽", "marquee": [
        "Inter Miami CF", "New York Red Bulls", "New York City FC", "LA Galaxy", "Los Angeles Football Club"]},
}
SPORTS_CLASSIFICATIONS = list(dict.fromkeys(t["classification"] for t in SPORTS_TEAMS.values()))
# Opponent nicknames that are two words ("Blue Jackets", not "Jackets"). Soccer clubs go by the city part instead
# (FC/CF/SC dropped): "Union vs. Inter Miami".
TWO_WORD_NICKNAMES = {"Blue Jackets", "Maple Leafs", "Red Wings", "Golden Knights", "Red Sox", "White Sox",
                      "Blue Jays"}
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
    r"\bmust have ticket\b",  # arena add-ons ("Flyers Pregame Tours - MUST HAVE TICKET TO GAME")
]

# --- Comedy (creator-roadmap P2-14) ---
# Stand-up from Ticketmaster's comedy feed (~1 API call a run). Scored like a show (venue + tour size; no Deezer
# fans, a comedian's name can match a musician's), and kept out of the music formats (stacked month, your lane).
COMEDY_CLASSIFICATION = "comedy"

# --- Philly experiences (creator-roadmap P2-14, survey in docs/agent-notes/experience-sources.md) ---
# Markets, festivals, seasonal events and anything you add by hand: segment "Experience", category in `genre`.
# Sources publish their own structured listings; robots.txt is checked before every read and a failed source
# keeps its events (it never erases them). `base` is the notability of a typical listing from that source.
EXPERIENCE_SOURCES = [
    # The Events Calendar's public API. Recurring dates (a market open daily) collapse into one event with a range.
    {"key": "ccd", "name": "Center City District", "kind": "tribe", "url": "https://www.centercityphila.org",
     "base": 50, "terms": "robots.txt allows; public Events Calendar API; no terms page (checked 2026-10-24)"},
    # Event list page, then one iCal file per event (the site's own "Add to calendar" link).
    {"key": "drwc", "name": "Delaware River Waterfront", "kind": "drwc",
     "url": "https://www.delawareriverwaterfront.com", "base": 60,
     "terms": "robots.txt allows the list page and iCal links (checked 2026-10-24)"},
]
EXPERIENCE_LOOKAHEAD_DAYS = 120
EXPERIENCE_LOOKBACK_DAYS = 120   # read this far back too, so a series that already started keeps its first date
EXPERIENCE_MAX_PAGES = 8         # 50 listings a page; CCD had 160 upcoming (2026-10-24)
EXPERIENCE_DELAY_SECONDS = 2     # between requests to the same site
# Words in a listing's name that make it video-worthy, or not. First the boosts, then the cuts.
EXPERIENCE_BIG_WORDS = [r"\bmarkets?\b", r"\bfest(ival)?s?\b", r"\bfireworks\b", r"\bparade\b", r"\blights?\b",
                        r"\blantern", r"\bwinterfest\b", r"\bopen streets\b", r"\blunar new year\b",
                        r"\btree lighting\b", r"\bopening (weekend|day|night)\b", r"\bnight market\b",
                        r"\bfood\b", r"\bpop-?up\b", r"\bholiday\b", r"\bhalloween\b", r"\bparty\b"]
EXPERIENCE_SMALL_WORDS = [r"\bhappy hour\b", r"\btrivia\b", r"\bgame night\b", r"\bclass(es)?\b",
                          r"\bvolunteer\b", r"\bworkshop\b", r"\bmeeting\b", r"\bbook swap\b", r"\bsocials?\b"]
EXPERIENCE_BIG_BONUS = 20
EXPERIENCE_SMALL_PENALTY = 25
EXPERIENCE_MANUAL_SCORE = 80      # you added it yourself: it's worth a look
EXPERIENCE_MIN_SCORE = 65         # single-event idea at or above this
EXPERIENCE_ALERT_DAYS = 21        # ...when it starts within this many days (or is on now)
# Categories (stored as the event's genre). Hand-added events pick one; source listings get one from their name.
EXPERIENCE_CATEGORIES = ["Food & Drink", "Market", "Festival", "Holiday", "Outdoors", "Arts", "Nightlife", "Other"]
EXPERIENCE_CATEGORY_WORDS = [  # first match wins
    ("Market", r"\bmarkets?\b|\bbazaar\b|\bflea\b"), ("Holiday", r"\bholiday|christmas|winterfest|tree lighting|"
     r"\bnew year|halloween|lunar new year|valentine"), ("Festival", r"\bfest(ival)?s?\b|\bparade\b|\bfireworks\b"),
    ("Food & Drink", r"\bfood\b|\btasting\b|\bbeer\b|\bwine\b|\bcocktail|\bwhiskey|\bbourbon"),
    ("Outdoors", r"\bopen streets\b|\bpark\b|\bgarden\b|\bbike\b|\bskating\b|\brink\b"),
]
# "Things to do this weekend": experiences and comedy, plus at most this many games and concerts.
THINGS_TO_DO_MIN = 3
THINGS_TO_DO_MAX = 8
THINGS_TO_DO_MIN_SCORE = 50       # experiences below this (a routine happy hour) stay out
THINGS_TO_DO_COMEDY = 3
THINGS_TO_DO_GAMES = 1
THINGS_TO_DO_CONCERTS = 2
WEEKEND_MAX_GAMES = 2             # the music weekend roundup keeps at most this many games

# Music news (tracker/news.py, docs/agent-notes/music-roadmap.md M1): public RSS/Atom feeds, read every 30 minutes
# by the PhillyEvents-News task with conditional requests (most reads are a 304 with no body). Feed URLs are public.
# terms: checked 2026-10-23: robots.txt allows the feed path and any crawl-delay is <= 10 s. Dropped then: Dancing
# Astronaut (feed hangs past 40 s), HipHopDX (stale), Complex / Pigeons & Planes / Resident Advisor (no feed).
# Reddit is not used (decided 2026-10-23: the rap feeds replace it). `poll_minutes` slows one site if needed.
NEWS_TERMS_CHECKED = "robots.txt allows the feed, crawl-delay <= 10 s (checked 2026-10-23)"
NEWS_SOURCES = [
    {"key": "pitchfork-news", "name": "Pitchfork news", "url": "https://pitchfork.com/feed/feed-news/rss", "niches": ["indie", "rap", "pop"]},
    {"key": "pitchfork-reviews", "name": "Pitchfork reviews", "url": "https://pitchfork.com/feed/feed-album-reviews/rss", "niches": ["indie", "rap", "pop"]},
    {"key": "stereogum", "name": "Stereogum", "url": "https://www.stereogum.com/feed/", "niches": ["indie", "pop"]},
    {"key": "fader", "name": "The FADER", "url": "https://www.thefader.com/feed", "niches": ["rap", "hyperpop", "rnb"]},
    {"key": "rated-rnb", "name": "Rated R&B", "url": "https://ratedrnb.com/feed/", "niches": ["rnb"]},
    {"key": "mixmag", "name": "Mixmag", "url": "https://mixmag.net/rss.xml", "niches": ["edm"]},  # no pubDate: fetched_at stands in
    {"key": "line-of-best-fit", "name": "Line of Best Fit", "url": "https://www.thelineofbestfit.com/feed", "niches": ["indie", "pop"]},
    {"key": "billboard", "name": "Billboard", "url": "https://www.billboard.com/feed/", "niches": ["pop", "rap", "rnb"]},
    {"key": "xxl", "name": "XXL", "url": "https://www.xxlmag.com/feed/", "niches": ["rap"]},
    {"key": "hotnewhiphop", "name": "HotNewHipHop", "url": "https://www.hotnewhiphop.com/feed", "niches": ["rap"]},
    {"key": "allhiphop", "name": "AllHipHop", "url": "https://allhiphop.com/feed/", "niches": ["rap"]},
    {"key": "the-source", "name": "The Source", "url": "https://thesource.com/feed/", "niches": ["rap"]},
    {"key": "vibe", "name": "Vibe", "url": "https://www.vibe.com/feed/", "niches": ["rap", "rnb"]},
    {"key": "uproxx-music", "name": "Uproxx Music", "url": "https://uproxx.com/music/feed/", "niches": ["rap", "pop", "indie"]},
]
# YouTube (roadmap M8), through the YouTube Data API v3: youtube.com's robots.txt disallows /feeds/videos.xml, so the
# channel RSS feeds are not used. Off until YOUTUBE_API_KEY is set (free; Google Cloud console, YouTube Data API v3).
# Each read costs 1 quota unit (of 10,000 a day); we stay under YOUTUBE_DAILY_UNITS. Outlet channels below; your
# artists' own channels come from MusicBrainz (watchlist.resolve_ids) and are stored in the database, not here.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_SOURCES = [  # channel ids from each handle page's canonical link, 2026-10-24
    {"key": "yt-colors", "name": "COLORS (YouTube)", "channel_id": "UC2Qw1dzXDBAZPwS7zm37g8g", "niches": ["rnb", "rap", "pop"]},
    {"key": "yt-lyrical-lemonade", "name": "Lyrical Lemonade (YouTube)", "channel_id": "UCtylTUUVIGY_i5afsQYeBZA", "niches": ["rap"]},
    {"key": "yt-elevator", "name": "Elevator (YouTube)", "channel_id": "UCvOT2VGzTrm-qm5fXkMCduw", "niches": ["rap"]},
]
YOUTUBE_OUTLET_POLL_MINUTES = 60    # outlets upload a few times a day
YOUTUBE_ARTIST_POLL_MINUTES = 360   # an artist's own channel, every 6 hours (~4 units a day each)
YOUTUBE_READS_PER_CHECK = 30        # channel reads per 30-min news check, oldest-read first
YOUTUBE_DAILY_UNITS = 3000          # our own cap, well under the API's 10,000
YOUTUBE_ITEMS = 10                  # latest uploads per read
YOUTUBE_TERMS_NOTE = "YouTube Data API v3 within its quota (robots.txt disallows the channel feeds; checked 2026-10-24)"

NEWS_POLL_MINUTES = 30          # default per source; a source that keeps failing waits longer (NEWS_BACKOFF_MINUTES)
NEWS_BACKOFF_MINUTES = (30, 60, 120)  # after 1, 2, 3+ failures in a row; back to normal on the next success
NEWS_MAX_AGE_DAYS = 14          # items older than this (by the feed's own date) aren't stored
NEWS_TIMEOUT = 25               # seconds per feed
NEWS_MAX_BYTES = 5_000_000      # a feed bigger than this is refused (the largest seen is ~1 MB)
NEWS_EXCERPT_CHARS = 300        # the feed's own summary, cut short; never the article body
CATALOG_ARTISTS_PER_CHECK = 16  # release catalogs per 30-min news check (Deezer + iTunes at ~20/min): each artist ~every 10 h
WATCHLIST_IDS_PER_RUN = 40     # artists per 6:30 / 4:30 run: Deezer + MusicBrainz id + MusicBrainz links (~100 s at 1/s)
NEWS_USER_AGENT = "PhillyEventsTracker/0.1 (personal project; reads public music-news feeds every 30 minutes)"
