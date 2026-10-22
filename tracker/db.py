"""SQLite schema and connection helper.

Design notes:
- `events` holds the latest known state of each Ticketmaster event.
- `changes` is an append-only log of what moved between runs (new, price, status...).
  Detectors read from here, so "what's new" is always relative to the last run.
- `alerts` are deduplicated by `key`, so re-running never spams the same alert.
- `drafts` are LLM outputs tied to an alert, with a review status you control.
"""
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT NOT NULL,
    events_fetched INTEGER DEFAULT 0,
    api_calls INTEGER DEFAULT 0,
    ok INTEGER DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS source_runs (
    run_id INTEGER REFERENCES runs(id),
    source TEXT NOT NULL,           -- venue calendar key, e.g. "underground-arts"
    ok INTEGER NOT NULL,
    count INTEGER,                  -- shows kept (after dropping Ticketmaster duplicates)
    error TEXT,
    PRIMARY KEY (run_id, source)
);

CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    state TEXT,
    tier INTEGER
);

CREATE TABLE IF NOT EXISTS attractions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    genre TEXT,
    subgenre TEXT,
    tm_upcoming_total INTEGER,      -- how many events TM lists for them worldwide (tour size proxy)
    fans INTEGER,                   -- Deezer fan count, NULL if unknown/unmatched
    popularity INTEGER,             -- 0-100 derived from fans (log scale), NULL if unknown
    popularity_checked_at TEXT,
    setlistfm_mbid TEXT,            -- MusicBrainz id used by setlist.fm
    last_area_show_date TEXT,       -- most recent past show within the radius on setlist.fm (YYYY-MM-DD)
    last_area_show_venue TEXT,
    last_area_show_acts INTEGER,    -- artists with setlists at that venue that day (5+ = festival/radio show)
    history_checked_back_to TEXT,   -- oldest setlist we looked at (how far back "no show found" goes)
    history_checked_at TEXT,
    genre_source TEXT,              -- NULL = Ticketmaster's classification; 'tm_search' = matched by name (genres.py)
    genre_checked_at TEXT,          -- last Ticketmaster name lookup for a venue-calendar act
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT,
    image_url TEXT,
    local_date TEXT,                -- YYYY-MM-DD (venue local time)
    local_time TEXT,                -- HH:MM:SS or NULL (TBA)
    status TEXT,                    -- onsale, offsale, cancelled, postponed, rescheduled
    venue_id TEXT REFERENCES venues(id),
    segment TEXT,
    genre TEXT,
    subgenre TEXT,
    price_min REAL,
    price_max REAL,
    currency TEXT,
    onsale_start TEXT,              -- public on-sale, UTC ISO
    onsale_end TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_changed TEXT,
    active INTEGER NOT NULL DEFAULT 1  -- 0 once it drops out of the feed
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(local_date);

CREATE TABLE IF NOT EXISTS event_attractions (
    event_id TEXT REFERENCES events(id),
    attraction_id TEXT REFERENCES attractions(id),
    position INTEGER,               -- 0 = headliner
    PRIMARY KEY (event_id, attraction_id)
);

CREATE TABLE IF NOT EXISTS presales (
    event_id TEXT REFERENCES events(id),
    name TEXT NOT NULL,
    start_utc TEXT,
    end_utc TEXT,
    PRIMARY KEY (event_id, name, start_utc)
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(id),
    event_id TEXT REFERENCES events(id),
    kind TEXT NOT NULL,             -- new, status, price, date, onsale, presale_added, removed
    old_value TEXT,
    new_value TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_changes_run ON changes(run_id);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,       -- dedupe key, e.g. "presale:<event>:<name>:<start>"
    kind TEXT NOT NULL,             -- new_tour, presale_soon, weekend, stacked_month, status_change
    title TEXT NOT NULL,
    score REAL DEFAULT 0,
    facts TEXT NOT NULL,            -- JSON: the only facts the LLM may use
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',  -- new, drafted, dismissed
    updated_at TEXT,                -- last time a run changed its facts (NULL = never changed)
    event_ids TEXT,                 -- JSON list of the events this video idea is about
    post_by TEXT,                   -- UTC ISO: the video is most useful if it's up by then (NULL = news, see opportunities.py)
    expires_at TEXT,                -- UTC ISO: after this the idea is stale
    shortlisted INTEGER NOT NULL DEFAULT 0,  -- you starred it in the queue
    snoozed_until TEXT,             -- UTC ISO: hidden from the queue until then ("Later")
    note TEXT                       -- your note on the idea
);

-- Earlier versions of an alert's facts, kept whenever a run refreshes them (see detectors.save_alerts).
CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER NOT NULL REFERENCES alerts(id),
    title TEXT NOT NULL,
    score REAL,
    facts TEXT NOT NULL,
    replaced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER REFERENCES alerts(id),
    format TEXT NOT NULL,
    model TEXT,
    hook TEXT,
    script TEXT,
    caption TEXT,
    hashtags TEXT,
    onscreen TEXT,                  -- JSON list of on-screen text lines (built from facts, not the LLM)
    warnings TEXT,                  -- JSON list: things in the LLM text not found in the facts
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, approved, filmed, rejected
    notes TEXT,
    facts TEXT,                     -- JSON: the alert's facts this draft was written from
    angle TEXT                      -- angles.py key the draft was written from (NULL = default)
);

-- Videos you actually published, logged by hand (nothing here is read from TikTok). See tracker/posts.py.
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER REFERENCES alerts(id),   -- NULL for videos that didn't come from an alert
    draft_id INTEGER REFERENCES drafts(id),
    platform TEXT NOT NULL DEFAULT 'tiktok',
    url TEXT,
    title TEXT NOT NULL,
    posted_at TEXT NOT NULL,        -- UTC ISO
    format TEXT,                    -- presale, weekend, stacked_month... (the alert's format, or chosen by hand)
    video_style TEXT,               -- voiceover, slideshow, footage, face-to-camera, other
    hook TEXT,                      -- the hook you actually used
    length_seconds INTEGER,
    notes TEXT,
    facts_snapshot TEXT,            -- JSON: the alert's facts when you posted (what we knew then, no hindsight)
    opportunity_snapshot TEXT,      -- JSON: the queue's score and reasons when you posted (opportunities.snapshot)
    created_at TEXT NOT NULL
);

-- Numbers copied from TikTok analytics at some moment. NULL = not recorded (never "zero").
CREATE TABLE IF NOT EXISTS post_metrics (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,      -- UTC ISO; compared with posted_at to get the post's age
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    saves INTEGER,
    followers_gained INTEGER,
    avg_watch_seconds REAL,
    completion_rate REAL,           -- percent watched to the end, 0-100
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_post_metrics_post ON post_metrics(post_id);

-- Web lookups (tracker/context.py): reading material for you, never facts for drafts.
CREATE TABLE IF NOT EXISTS web_context (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,          -- cache key: casefolded artist name
    alert_id INTEGER,               -- the idea it was looked up for (no FK: the lookup outlives the alert)
    provider TEXT NOT NULL,
    prompt TEXT NOT NULL,
    answer TEXT,
    sources TEXT,                   -- JSON list of {id, title, url, date}
    cost_usd REAL,
    error TEXT,                     -- set when the call failed (still counts against the daily cap)
    trigger TEXT NOT NULL,          -- manual, auto
    created_at TEXT NOT NULL        -- UTC ISO
);
CREATE INDEX IF NOT EXISTS idx_web_context_subject ON web_context(subject, created_at);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may close a request's connection on a different worker thread than
    # the one that opened it (sync dependency teardown), which sqlite3 otherwise refuses with a 500.
    # Each connection is still used by one request/job at a time.
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # lets the dashboard read while a run writes
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add them to an
# existing database, so add any that are missing. (Old unused columns are left in place.)
_ADDED_COLUMNS = {
    "attractions": {"fans": "INTEGER", "popularity": "INTEGER", "popularity_checked_at": "TEXT",
                    "setlistfm_mbid": "TEXT", "last_area_show_date": "TEXT", "last_area_show_venue": "TEXT", "last_area_show_acts": "INTEGER",
                    "history_checked_back_to": "TEXT", "history_checked_at": "TEXT",
                    "genre_source": "TEXT", "genre_checked_at": "TEXT"},  # genres.py: NULL source = Ticketmaster's own
    "alerts": {"updated_at": "TEXT", "event_ids": "TEXT", "post_by": "TEXT", "expires_at": "TEXT",
               "shortlisted": "INTEGER NOT NULL DEFAULT 0", "snoozed_until": "TEXT", "note": "TEXT"},
    "drafts": {"facts": "TEXT", "angle": "TEXT"},
    "posts": {"opportunity_snapshot": "TEXT"},
}
# Run once, right after the column is added to an existing database.
_BACKFILL = {
    # Drafts written before this column existed were written from the alert's facts as they are now
    # (alert facts never changed before refreshing existed).
    ("drafts", "facts"): "UPDATE drafts SET facts = (SELECT facts FROM alerts WHERE alerts.id = drafts.alert_id)",
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                if (table, col) in _BACKFILL:
                    conn.execute(_BACKFILL[(table, col)])
    conn.commit()
