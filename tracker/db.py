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
    deezer_genre TEXT,              -- most common genre of their Deezer releases, in TM's words; NULL = unclear
    deezer_genre_checked_at TEXT,
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

-- Events you add yourself (P2-14): every fact here is typed by you, never looked up. Each run copies the
-- active ones into `events` as "man:<id>" (segment Experience), so they're ideas like any other.
CREATE TABLE IF NOT EXISTS manual_events (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    start_date TEXT NOT NULL,       -- YYYY-MM-DD
    end_date TEXT,                  -- last day, for multi-day events
    start_time TEXT,                -- HH:MM or NULL
    venue TEXT NOT NULL,
    city TEXT,
    price_min REAL,                 -- NULL = not stated; 0 only when you ticked "free"
    price_max REAL,
    url TEXT,
    notes TEXT,                     -- for you; never shown to the model
    created_at TEXT NOT NULL,
    updated_at TEXT,
    archived INTEGER NOT NULL DEFAULT 0
);

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
    note TEXT,                      -- your note on the idea
    planned_for TEXT,               -- UTC ISO: when you plan to post it (planner.py); nothing posts by itself
    series TEXT                     -- your series label ("Philly this weekend"); NULL = the format's default
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
    angle TEXT,                     -- angles.py key the draft was written from (NULL = default)
    instructions TEXT               -- JSON: your note + shows added/left out for this draft (redraft.py)
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
    series TEXT,                    -- recurring series it belongs to (the alert's series, or typed in)
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

-- Trends you noticed, typed in by hand (tracker/trends.py). Evidence for the queue's "search" component.
CREATE TABLE IF NOT EXISTS trend_notes (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,             -- sound, search, hashtag, topic, other
    text TEXT NOT NULL,             -- what you saw, e.g. a search term or the sound's name
    terms TEXT NOT NULL,            -- JSON list: artists, genres, venues or keywords it should match
    scope TEXT NOT NULL,            -- philly, national, unknown
    confidence TEXT NOT NULL,       -- low, medium, high
    momentum TEXT,                  -- rising, stable, uncertain (your read; never computed)
    source_url TEXT,
    notes TEXT,
    observed_at TEXT NOT NULL,      -- UTC ISO: when you saw it
    expires_at TEXT NOT NULL,       -- UTC ISO: stops counting after this
    created_at TEXT NOT NULL,
    updated_at TEXT
);

-- 9:16 slide decks (tracker/slides.py): manifest built from facts + your edits, one deck per alert.
CREATE TABLE IF NOT EXISTS slide_decks (
    alert_id INTEGER PRIMARY KEY REFERENCES alerts(id) ON DELETE CASCADE,
    base TEXT NOT NULL,             -- JSON manifest as built from `facts` (no edits)
    facts TEXT NOT NULL,            -- JSON: the alert's facts the base was built from
    overrides TEXT NOT NULL,        -- JSON: your edits by slide id (text, hidden, image + credit/rights)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Writes made from your phone through the Cloudflare Tunnel (tracker/remote.py), for the record.
CREATE TABLE IF NOT EXISTS remote_edits (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    email TEXT NOT NULL,            -- the Cloudflare Access login
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status INTEGER NOT NULL         -- HTTP status the dashboard answered with
);

-- Music news (tracker/news.py): public feeds, read with conditional requests. Seeded from config.NEWS_SOURCES.
-- A failing source keeps its articles (same rule as venue calendars).
CREATE TABLE IF NOT EXISTS news_sources (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,       -- config key, e.g. "pitchfork-news"
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'rss',  -- rss, youtube (Data API, roadmap M8)
    url TEXT NOT NULL,
    niches TEXT,                    -- JSON list, e.g. ["rap", "rnb"]
    enabled INTEGER NOT NULL DEFAULT 1,  -- 0 once it's removed from config (its articles stay)
    poll_minutes INTEGER NOT NULL DEFAULT 30,
    etag TEXT,                      -- for If-None-Match
    last_modified TEXT,             -- for If-Modified-Since
    last_fetched_at TEXT,           -- UTC ISO, any outcome
    last_ok_at TEXT,                -- last 200 or 304
    last_not_modified_at TEXT,      -- last 304 (nothing new, no body)
    last_error TEXT,                -- NULL once it works again
    failures INTEGER NOT NULL DEFAULT 0,  -- in a row; sets the back-off (config.NEWS_BACKOFF_MINUTES)
    terms_note TEXT                 -- what the site allows and when that was checked
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES news_sources(id),
    guid TEXT NOT NULL,
    url TEXT,
    title TEXT NOT NULL,
    excerpt TEXT,                   -- the feed's own summary, cut short (no article bodies)
    published_at TEXT,              -- UTC ISO from the feed; NULL when the feed has none (fetched_at stands in)
    fetched_at TEXT NOT NULL,       -- UTC ISO: first time we saw it
    categories TEXT,                -- JSON list from the feed
    title_hash TEXT,                -- normalized title, for clustering across sources later (roadmap M3)
    first_read INTEGER NOT NULL DEFAULT 0,  -- 1 = already in the feed the first time we read the source
    UNIQUE (source_id, guid)
);
CREATE INDEX IF NOT EXISTS idx_articles_seen ON articles(fetched_at);
CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(source_id, url);

-- Music watchlist (tracker/watchlist.py, roadmap M2): one row per artist. Seeded from your listening history (names
-- and tiers only; play counts stay in plays.db and are read live). Your edits (user_edited=1) survive re-seeds.
CREATE TABLE IF NOT EXISTS artist_identities (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,       -- listening.artist_key(name)
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',  -- JSON list of other spellings to match
    tier TEXT NOT NULL,             -- core, adjacent, watch, breakout; 'dropped' = fell out of your rotation
    origin TEXT NOT NULL,           -- listening, manual (related / editorial later, M7)
    muted INTEGER NOT NULL DEFAULT 0,  -- never matched
    user_edited INTEGER NOT NULL DEFAULT 0,  -- you changed it: re-seeding leaves it alone
    common_word INTEGER NOT NULL DEFAULT 0,  -- the name is also an ordinary word (auto, from the headlines)
    common_word_override INTEGER,   -- your call: NULL = automatic, 0 = no, 1 = yes
    ambiguous_name INTEGER NOT NULL DEFAULT 0,  -- MusicBrainz has 2+ artists by this exact name
    mbid TEXT,
    deezer_id INTEGER,
    attraction_id TEXT,             -- the events pipeline's artist, matched by name key
    ids_checked_at TEXT,            -- last Deezer / MusicBrainz lookup
    itunes_id INTEGER,              -- iTunes artist id (tracker/catalog.py), found on the first catalog check
    releases_checked_at TEXT,       -- last catalog check (Deezer + iTunes), tracker/catalog.py
    seen_live INTEGER NOT NULL DEFAULT 0,  -- you've seen them live (your call): their tour news scores higher (M5)
    created_at TEXT NOT NULL
);

-- How an article was tied to an artist. Rebuilt when the watchlist changes; added to as new articles arrive.
CREATE TABLE IF NOT EXISTS article_artists (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    artist_id INTEGER NOT NULL REFERENCES artist_identities(id) ON DELETE CASCADE,
    match TEXT NOT NULL,            -- exact, alias, owner (an upload on the artist's own YouTube channel, M8)
    location TEXT NOT NULL,         -- title, excerpt
    confidence TEXT NOT NULL,       -- high (title), medium (excerpt), low (a common-word name next to a music word)
    PRIMARY KEY (article_id, artist_id)
);
CREATE INDEX IF NOT EXISTS idx_article_artists_artist ON article_artists(artist_id);

-- Music stories (tracker/stories.py, roadmap M3): articles about the same thing grouped into one card. Rebuilt
-- after each news check; a story you acted on (feedback / alert_id) keeps its id and those fields.
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,       -- "<primary artist id>:<type family>:<first article id>", stable across rebuilds
    story_type TEXT NOT NULL,       -- album_announcement, single_release, tour, chart, legal... (stories.TYPES)
    rule TEXT,                      -- why: the classifier rule that fired, e.g. "tour: 'announces 2027 tour'"
    primary_artist_id INTEGER REFERENCES artist_identities(id) ON DELETE SET NULL,
    other_artist_ids TEXT NOT NULL DEFAULT '[]',  -- JSON list
    title TEXT NOT NULL,            -- headline of the first article
    release_title TEXT,             -- album/song name when a headline states it
    release_status TEXT,            -- rumored, teased, announced, scheduled, released (NULL = not about a release)
    release_date TEXT,              -- YYYY-MM-DD when an article states one (first stated; see verification)
    verification TEXT NOT NULL DEFAULT 'unverified',  -- unverified, corroborated (2+ sources), confirmed (M4), conflicting
    sensitive INTEGER NOT NULL DEFAULT 0,  -- deaths, arrests, lawsuits, health: never auto-promoted, no template hooks
    dated INTEGER NOT NULL DEFAULT 1,  -- 0 = only undated items from before tracking began: time unknown, listed last
    origin TEXT NOT NULL DEFAULT 'news',  -- news (from articles) or catalog (a release no feed covered, M4)
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    feedback TEXT,                  -- your call (M5): interested, not_my_lane, already_covered
    alert_id INTEGER,               -- once sent to the queue (M5)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stories_seen ON stories(last_seen);

CREATE TABLE IF NOT EXISTS story_articles (
    story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    PRIMARY KEY (story_id, article_id)
);

-- Every factual claim with where it came from (roadmap section 5). Drafts read facts from here only.
CREATE TABLE IF NOT EXISTS story_evidence (
    id INTEGER PRIMARY KEY,
    story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,             -- article (catalog / official later, M4 / M8)
    provider TEXT NOT NULL,         -- source name, e.g. "Pitchfork news"
    url TEXT,
    excerpt TEXT,                   -- the words the claim was read from
    claim TEXT NOT NULL,            -- release_date, title, status
    value TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_story_evidence_story ON story_evidence(story_id);

-- Everything you did with a story (tracker/music.py, roadmap M5): feedback buttons and Send to queue. Kept when the
-- story itself is rebuilt away; M9's "merge Music news into What to post" decision is measured from it.
CREATE TABLE IF NOT EXISTS story_actions (
    id INTEGER PRIMARY KEY,
    story_id INTEGER,
    story_key TEXT NOT NULL,
    artist_id INTEGER,
    story_type TEXT,
    action TEXT NOT NULL,           -- interested, not_my_lane, already_covered, cleared, sent, auto_sent
    score REAL,                     -- the story's music score when you acted
    at TEXT NOT NULL
);

-- Catalog records per provider (tracker/catalog.py, roadmap M4), side by side so gaps and disagreements show.
-- Only recent and upcoming releases are kept (catalog.KEEP_DAYS); nothing here is guessed.
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY,
    artist_id INTEGER NOT NULL REFERENCES artist_identities(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,         -- deezer, itunes
    provider_id TEXT NOT NULL,
    title TEXT NOT NULL,            -- as the provider writes it ("Glass Hours - Single")
    title_key TEXT NOT NULL,        -- normalized for matching across providers and headlines (catalog.title_key)
    record_type TEXT,               -- album, ep, single (compile = compilation, Deezer)
    release_date TEXT NOT NULL,     -- YYYY-MM-DD; later than today = a pre-order / scheduled release
    url TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE (artist_id, provider, provider_id)  -- a joint album is listed under each of its artists
);
CREATE INDEX IF NOT EXISTS idx_releases_artist ON releases(artist_id, release_date);

-- Artists you don't follow yet (tracker/discovery.py, roadmap M7): Last.fm similar artists and niche tags, and guests
-- on your artists' releases. Each keeps its evidence; accepting one adds it to the watchlist, dismissing hides it.
CREATE TABLE IF NOT EXISTS artist_candidates (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,       -- listening.artist_key(name)
    name TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]',  -- JSON [{kind: similar|tag|feature, of, detail, weight}], from the last refresh
    tags TEXT NOT NULL DEFAULT '[]',      -- Last.fm's top tags for them
    mbid TEXT,
    deezer_id INTEGER,
    lastfm_url TEXT,
    status TEXT NOT NULL DEFAULT 'new',  -- new, accepted, dismissed
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    decided_at TEXT
);

-- Daily audience and attention numbers for momentum (M7), for watchlist artists and candidates alike (by name key).
-- Never back-filled or guessed: a day that wasn't observed stays missing.
CREATE TABLE IF NOT EXISTS artist_metrics (
    artist_key TEXT NOT NULL,
    provider TEXT NOT NULL,         -- deezer, lastfm, listenbrainz, news
    metric TEXT NOT NULL,           -- fans, listeners, playcount, listens, mentions (distinct outlets, last 7 days)
    value INTEGER NOT NULL,
    observed_on TEXT NOT NULL,      -- ET date
    PRIMARY KEY (artist_key, provider, metric, observed_on)
);

-- Cached third-party lookups (Last.fm similar artists and tag lists, a week; see discovery.py)
CREATE TABLE IF NOT EXISTS api_cache (
    key TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- Claude API calls for drafts (tracker/llm.py): the daily cap and the cost on the Runs tab.
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,               -- UTC ISO
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    error TEXT                      -- set when the call failed (it still counts against the cap)
);
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
    # P2-14: last day of a multi-day event (a market open for six weeks), and which source listed it
    "events": {"end_date": "TEXT", "source": "TEXT"},
    "attractions": {"fans": "INTEGER", "popularity": "INTEGER", "popularity_checked_at": "TEXT",
                    "setlistfm_mbid": "TEXT", "last_area_show_date": "TEXT", "last_area_show_venue": "TEXT", "last_area_show_acts": "INTEGER",
                    "history_checked_back_to": "TEXT", "history_checked_at": "TEXT",
                    "genre_source": "TEXT", "genre_checked_at": "TEXT",  # genres.py: NULL source = Ticketmaster's own
                    "deezer_genre": "TEXT", "deezer_genre_checked_at": "TEXT"},  # fetchers/deezer.py
    "alerts": {"updated_at": "TEXT", "event_ids": "TEXT", "post_by": "TEXT", "expires_at": "TEXT",
               "shortlisted": "INTEGER NOT NULL DEFAULT 0", "snoozed_until": "TEXT", "note": "TEXT",
               "planned_for": "TEXT", "series": "TEXT"},
    "drafts": {"facts": "TEXT", "angle": "TEXT", "instructions": "TEXT"},
    "posts": {"opportunity_snapshot": "TEXT", "series": "TEXT"},
    "artist_identities": {"itunes_id": "INTEGER", "releases_checked_at": "TEXT",  # catalog.py (M4)
                          "seen_live": "INTEGER NOT NULL DEFAULT 0",  # music.py (M5)
                          "links": "TEXT",  # JSON [{type: homepage|youtube|blog, url}] from MusicBrainz (M8)
                          "links_checked_at": "TEXT"},
    "stories": {"origin": "TEXT NOT NULL DEFAULT 'news'"},
    # M8: an artist's own channel (artist_id = whose), and where a source came from (config, or musicbrainz links)
    "news_sources": {"artist_id": "INTEGER", "origin": "TEXT NOT NULL DEFAULT 'config'"},
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
