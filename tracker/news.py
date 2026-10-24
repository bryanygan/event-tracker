"""Music news, step 1 (docs/agent-notes/music-roadmap.md M1): read the feeds in config.NEWS_SOURCES and store new
articles. No matching, scoring, LLM or web lookups yet; it finishes in seconds.

Usage (what the PhillyEvents-News task runs every 30 minutes):
    .venv\\Scripts\\python -m tracker.news

Takes the pipeline's one-run-at-a-time lock and skips quietly if a run holds it (the feeds keep at least a day of
items, so the next check catches up). Each source is read at most every `poll_minutes`, longer after failures.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import requests

from . import catalog, config, db, discovery, music, music_formats, store, stories, watchlist
from .fetchers import news_rss

log = logging.getLogger("tracker.news")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _upsert_source(conn, key: str, name: str, url: str, kind: str, niches: list, poll_minutes: int, terms_note: str,
                   artist_id: int | None = None, origin: str = "config") -> None:
    conn.execute("""INSERT INTO news_sources (key, name, url, kind, niches, poll_minutes, terms_note, artist_id, origin,
                                              enabled)
                    VALUES (?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(key) DO UPDATE SET name=excluded.name, url=excluded.url, kind=excluded.kind,
                        niches=excluded.niches, poll_minutes=excluded.poll_minutes, terms_note=excluded.terms_note,
                        artist_id=excluded.artist_id, origin=excluded.origin, enabled=1""",
                 (key, name, url, kind, json.dumps(niches), poll_minutes, terms_note, artist_id, origin))


def seed_sources(conn) -> None:
    """Add or update sources from config (feeds and YouTube outlets); ones no longer in config are disabled, never
    deleted. Artist channels (origin 'musicbrainz') are synced separately, by sync_artist_channels."""
    keys = []
    for s in config.NEWS_SOURCES:
        keys.append(s["key"])
        _upsert_source(conn, s["key"], s["name"], s["url"], "rss", s.get("niches", []),
                       s.get("poll_minutes", config.NEWS_POLL_MINUTES), s.get("terms_note", config.NEWS_TERMS_CHECKED))
    for s in config.YOUTUBE_SOURCES:
        keys.append(s["key"])
        _upsert_source(conn, s["key"], s["name"], f"https://www.youtube.com/channel/{s['channel_id']}", "youtube",
                       s.get("niches", []), s.get("poll_minutes", config.YOUTUBE_OUTLET_POLL_MINUTES),
                       config.YOUTUBE_TERMS_NOTE)
    conn.execute(f"UPDATE news_sources SET enabled=0 WHERE origin='config' AND key NOT IN ({','.join('?' * len(keys))})"
                 if keys else "UPDATE news_sources SET enabled=0 WHERE origin='config'", keys)
    conn.commit()


ARTIST_CHANNELS_EACH = 2  # an artist's own channel and, say, their VEVO channel


def sync_artist_channels(conn) -> int:
    """A YouTube source for each watchlist artist's own channel, from the links MusicBrainz lists
    (watchlist.resolve_ids). Muted or dropped artists' channels are disabled. Returns how many are enabled."""
    from .fetchers import youtube
    marks = ",".join("?" * len(watchlist.MATCHED_TIERS))
    keys = []
    for a in conn.execute(f"""SELECT id, name, links FROM artist_identities
                              WHERE muted=0 AND tier IN ({marks}) AND links IS NOT NULL""", watchlist.MATCHED_TIERS):
        refs = [r for r in (youtube.channel_ref(l["url"]) for l in json.loads(a["links"]) if l["type"] == "youtube") if r]
        for ref in refs[:ARTIST_CHANNELS_EACH]:
            key = "yt-artist:" + next(iter(ref.values()))
            if key in keys:  # two artists listing one channel (a duo and a member): the first keeps it
                continue
            keys.append(key)
            url = f"https://www.youtube.com/channel/{ref['id']}" if "id" in ref else \
                f"https://www.youtube.com/{ref['handle']}" if "handle" in ref else f"https://www.youtube.com/user/{ref['user']}"
            _upsert_source(conn, key, f"{a['name']} (YouTube)", url, "youtube", [], config.YOUTUBE_ARTIST_POLL_MINUTES,
                           config.YOUTUBE_TERMS_NOTE + "; the artist's own channel, per MusicBrainz",
                           artist_id=a["id"], origin="musicbrainz")
    conn.execute(f"UPDATE news_sources SET enabled=0 WHERE origin='musicbrainz' AND key NOT IN ({','.join('?' * len(keys))})"
                 if keys else "UPDATE news_sources SET enabled=0 WHERE origin='musicbrainz'", keys)
    conn.commit()
    return len(keys)


# ---------- YouTube quota (each read is 1 unit; config.YOUTUBE_DAILY_UNITS is our own cap) ----------

def _units_key(now: datetime) -> str:
    return f"youtube:units:{now.date().isoformat()}"  # the API's quota resets at midnight Pacific; UTC is close enough


def youtube_units(conn, now: datetime) -> int:
    row = conn.execute("SELECT body FROM api_cache WHERE key=?", (_units_key(now),)).fetchone()
    return json.loads(row["body"]) if row else 0


def _add_units(conn, now: datetime, units: int) -> None:
    if units:
        conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES (?,?,?)",
                     (_units_key(now), json.dumps(youtube_units(conn, now) + units), now.isoformat()))


CHANNEL_CACHE_DAYS = 30


def _youtube_uploads(conn, client, source, now: datetime) -> list[dict]:
    """The channel's latest uploads. Its uploads-playlist id is looked up once and kept CHANNEL_CACHE_DAYS (a channel
    that doesn't exist is remembered too, so it costs nothing until then)."""
    from .fetchers import youtube
    cache_key = f"youtube:channel:{source['key']}"
    row = conn.execute("SELECT body, fetched_at FROM api_cache WHERE key=?", (cache_key,)).fetchone()
    if row and datetime.fromisoformat(row["fetched_at"]) > now - timedelta(days=CHANNEL_CACHE_DAYS):
        channel = json.loads(row["body"])
    else:
        ref = youtube.channel_ref(source["url"])
        channel = client.channel(ref) if ref else None
        conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES (?,?,?)",
                     (cache_key, json.dumps(channel), now.isoformat()))
    if not channel or not channel.get("uploads"):
        raise news_rss.FeedError("channel not found or has no uploads")
    return client.uploads(channel["uploads"])


def wait_minutes(source) -> int:
    """How long after the last read this source is due again."""
    if not source["failures"]:
        return source["poll_minutes"]
    backoff = config.NEWS_BACKOFF_MINUTES
    return max(source["poll_minutes"], backoff[min(source["failures"], len(backoff)) - 1])


def is_due(source, now: datetime) -> bool:
    if not source["last_fetched_at"]:
        return True
    # A couple of minutes' slack, so a task that starts a few seconds early doesn't skip a whole cycle
    due = datetime.fromisoformat(source["last_fetched_at"]) + timedelta(minutes=wait_minutes(source) - 2)
    return now >= due


def store_articles(conn, source, articles: list[dict], now: datetime) -> list[int]:
    """Insert articles not seen before (by guid, or by URL if the guid changed). Returns the new articles' ids."""
    first_read = source["last_ok_at"] is None
    oldest = (now - timedelta(days=config.NEWS_MAX_AGE_DAYS)).isoformat()
    new = []
    for a in articles:
        if a["published_at"] and a["published_at"] < oldest:
            continue
        if a["published_at"] and a["published_at"] > now.isoformat():
            # Some feeds date posts ahead (scheduled, or local time labelled +0000); it can't be newer than now
            a = {**a, "published_at": now.isoformat()}
        if a.get("url") and conn.execute("SELECT 1 FROM articles WHERE source_id=? AND url=?",
                                         (source["id"], a["url"])).fetchone():
            continue
        cur = conn.execute(
            """INSERT OR IGNORE INTO articles (source_id, guid, url, title, excerpt, published_at, fetched_at,
                                               categories, title_hash, first_read)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (source["id"], a["guid"], a.get("url"), a["title"], a.get("excerpt"), a["published_at"], now.isoformat(),
             json.dumps(a["categories"]), news_rss.title_hash(a["title"]), int(first_read)))
        if cur.rowcount:
            new.append(cur.lastrowid)
    return new


def read_source(conn, session, source, now: datetime, youtube_client=None) -> dict:
    """One conditional GET (a feed) or one API read (YouTube). On any failure the stored articles and validators
    stay as they were. A used-up YouTube quota is not the channel's fault: no failure is counted for it."""
    from .fetchers import youtube
    result = {"source": source["key"], "status": None, "new": 0, "new_ids": [], "error": None}
    try:
        if source["kind"] == "youtube":
            got = {"status": 200, "articles": _youtube_uploads(conn, youtube_client, source, now), "etag": None,
                   "last_modified": None}
        else:
            got = news_rss.fetch(session, source["url"], source["etag"], source["last_modified"])
        result["status"] = got["status"]
        if got["status"] == 200:
            articles = got["articles"] if "articles" in got else news_rss.parse(got["body"])
            result["new_ids"] = store_articles(conn, source, articles, now)
            result["new"] = len(result["new_ids"])
            conn.execute("""UPDATE news_sources SET etag=?, last_modified=?, last_ok_at=?, last_error=NULL,
                            failures=0, last_fetched_at=? WHERE id=?""",
                         (got["etag"], got["last_modified"], now.isoformat(), now.isoformat(), source["id"]))
        else:  # 304: nothing new, nothing to parse
            conn.execute("""UPDATE news_sources SET last_ok_at=?, last_not_modified_at=?, last_error=NULL, failures=0,
                            last_fetched_at=? WHERE id=?""", (now.isoformat(),) * 3 + (source["id"],))
    except youtube.QuotaExceeded as exc:
        result["error"], result["quota"] = str(exc), True
        conn.execute("UPDATE news_sources SET last_error=? WHERE id=?", (str(exc), source["id"]))
    except news_rss.FeedError as exc:
        result["error"] = str(exc)
        conn.execute("UPDATE news_sources SET last_error=?, failures=failures+1, last_fetched_at=? WHERE id=?",
                     (str(exc), now.isoformat(), source["id"]))
    conn.commit()
    return result


def _youtube_due(sources: list, now: datetime) -> list:
    """Due channels, longest-unread first, at most YOUTUBE_READS_PER_CHECK. None without an API key."""
    if not config.YOUTUBE_API_KEY:
        return []
    return sorted(sources, key=lambda s: s["last_fetched_at"] or "")[:config.YOUTUBE_READS_PER_CHECK]


def run(conn, session: requests.Session | None = None, now: datetime | None = None, youtube_client=None) -> dict:
    """Read every due source once (YouTube channels within the quota). Recorded in `runs` as source 'news' (new
    articles in events_fetched)."""
    from .fetchers import youtube
    seed_sources(conn)
    sync_artist_channels(conn)
    run_id = store.start_run(conn, "news")
    session = session or requests.Session()
    results = []
    try:
        due = [s for s in conn.execute("SELECT * FROM news_sources WHERE enabled=1 ORDER BY id")
               if is_due(s, now or _now())]
        for i, source in enumerate(s for s in due if s["kind"] != "youtube"):
            if i:
                time.sleep(0.5)  # every source is a different host; this just keeps the job gentle
            results.append(read_source(conn, session, source, now or _now()))
        channels = _youtube_due([s for s in due if s["kind"] == "youtube"], now or _now())
        youtube_client = youtube_client or (youtube.YouTubeClient() if channels else None)
        for source in channels:
            if youtube_units(conn, now or _now()) + 2 > config.YOUTUBE_DAILY_UNITS:  # a channel lookup + its uploads
                log.info("YouTube: today's %d-unit cap reached; the rest wait", config.YOUTUBE_DAILY_UNITS)
                break
            before = youtube_client.units
            r = read_source(conn, session, source, now or _now(), youtube_client)
            _add_units(conn, now or _now(), youtube_client.units - before)
            conn.commit()
            results.append(r)
            if r.get("quota"):
                break
        new = sum(r["new"] for r in results)
        # Tag the new articles with watchlist artists (the first time, build the watchlist from listening history)
        if not conn.execute("SELECT 1 FROM artist_identities LIMIT 1").fetchone():
            watchlist.refresh(conn)
        else:
            watchlist.rematch(conn, [i for r in results for i in r["new_ids"]])
        failed = [f"{r['source']} ({r['error']})" for r in results if r["error"]]
        # A few artists' release catalogs (Deezer + iTunes), then stories again so catalog evidence shows up
        try:
            releases = catalog.poll(conn)
            if releases["artists"]:
                stories.rebuild(conn)
        except Exception as exc:  # the feeds are the job; never lose them over a catalog problem
            log.warning("Catalog check failed: %s", exc)
            releases = {"artists": 0, "new": 0, "error": str(exc)}
        music.auto_send(conn)  # off unless config.MUSIC_AUTO_SEND (M9)
        try:  # New Music Friday and next month's albums, from the catalogs (music_formats.py, M6)
            music_formats.roundups(conn)
        except Exception as exc:
            log.warning("Music roundups failed: %s", exc)
        try:  # candidates weekly, mention counts daily, a slice of artist snapshots (discovery.py, M7)
            found = discovery.step(conn)
        except Exception as exc:
            log.warning("Discovery step failed: %s", exc)
            found = {"error": str(exc)}
        store.finish_run(conn, run_id, fetched=new, api_calls=len(results), ok=True,
                         error=("failed: " + ", ".join(failed)) if failed else None)
    except Exception as exc:
        store.finish_run(conn, run_id, fetched=sum(r["new"] for r in results), api_calls=len(results), ok=False,
                         error=str(exc))
        raise
    return {"run_id": run_id, "read": len(results), "new": new,
            "not_modified": sum(r["status"] == 304 for r in results), "failed": failed, "catalog": releases,
            "discovery": found}


def main(argv=None):
    from .run import RunBusy, setup_logging, single_run  # the pipeline's lock and log
    setup_logging()
    started = time.monotonic()
    try:
        with single_run(), closing(db.connect()) as conn:
            result = run(conn)
    except RunBusy as exc:
        log.info("Skipped this news check: %s", exc)
        return
    log.info("News check in %.0fs: %s", time.monotonic() - started, result)


if __name__ == "__main__":
    main()
