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
    spotify_id TEXT,
    spotify_popularity INTEGER,     -- 0-100, NULL if unknown
    spotify_followers INTEGER,
    spotify_genres TEXT,            -- comma separated
    spotify_checked_at TEXT,
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
    status TEXT NOT NULL DEFAULT 'new'  -- new, drafted, dismissed
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
    notes TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # lets the dashboard read while a run writes
    conn.executescript(SCHEMA)
    return conn
