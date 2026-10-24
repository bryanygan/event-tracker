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

from . import catalog, config, db, music, music_formats, store, stories, watchlist
from .fetchers import news_rss

log = logging.getLogger("tracker.news")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def seed_sources(conn) -> None:
    """Add or update sources from config; ones no longer in config are disabled, never deleted."""
    keys = []
    for s in config.NEWS_SOURCES:
        keys.append(s["key"])
        conn.execute("""INSERT INTO news_sources (key, name, url, niches, poll_minutes, terms_note, enabled)
                        VALUES (?,?,?,?,?,?,1)
                        ON CONFLICT(key) DO UPDATE SET name=excluded.name, url=excluded.url, niches=excluded.niches,
                            poll_minutes=excluded.poll_minutes, terms_note=excluded.terms_note, enabled=1""",
                     (s["key"], s["name"], s["url"], json.dumps(s.get("niches", [])),
                      s.get("poll_minutes", config.NEWS_POLL_MINUTES), s.get("terms_note", config.NEWS_TERMS_CHECKED)))
    conn.execute(f"UPDATE news_sources SET enabled=0 WHERE key NOT IN ({','.join('?' * len(keys))})" if keys
                 else "UPDATE news_sources SET enabled=0", keys)
    conn.commit()


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


def read_source(conn, session, source, now: datetime) -> dict:
    """One conditional GET. On any failure the stored articles and validators stay as they were."""
    result = {"source": source["key"], "status": None, "new": 0, "new_ids": [], "error": None}
    try:
        got = news_rss.fetch(session, source["url"], source["etag"], source["last_modified"])
        result["status"] = got["status"]
        if got["status"] == 200:
            articles = news_rss.parse(got["body"])
            result["new_ids"] = store_articles(conn, source, articles, now)
            result["new"] = len(result["new_ids"])
            conn.execute("""UPDATE news_sources SET etag=?, last_modified=?, last_ok_at=?, last_error=NULL,
                            failures=0, last_fetched_at=? WHERE id=?""",
                         (got["etag"], got["last_modified"], now.isoformat(), now.isoformat(), source["id"]))
        else:  # 304: nothing new, nothing to parse
            conn.execute("""UPDATE news_sources SET last_ok_at=?, last_not_modified_at=?, last_error=NULL, failures=0,
                            last_fetched_at=? WHERE id=?""", (now.isoformat(),) * 3 + (source["id"],))
    except news_rss.FeedError as exc:
        result["error"] = str(exc)
        conn.execute("UPDATE news_sources SET last_error=?, failures=failures+1, last_fetched_at=? WHERE id=?",
                     (str(exc), now.isoformat(), source["id"]))
    conn.commit()
    return result


def run(conn, session: requests.Session | None = None, now: datetime | None = None) -> dict:
    """Read every due source once. Recorded in `runs` as source 'news' (new articles in events_fetched)."""
    seed_sources(conn)
    run_id = store.start_run(conn, "news")
    session = session or requests.Session()
    results = []
    try:
        due = [s for s in conn.execute("SELECT * FROM news_sources WHERE enabled=1 ORDER BY id")
               if is_due(s, now or _now())]
        for i, source in enumerate(due):
            if i:
                time.sleep(0.5)  # every source is a different host; this just keeps the job gentle
            results.append(read_source(conn, session, source, now or _now()))
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
        store.finish_run(conn, run_id, fetched=new, api_calls=len(results), ok=True,
                         error=("failed: " + ", ".join(failed)) if failed else None)
    except Exception as exc:
        store.finish_run(conn, run_id, fetched=sum(r["new"] for r in results), api_calls=len(results), ok=False,
                         error=str(exc))
        raise
    return {"run_id": run_id, "read": len(results), "new": new,
            "not_modified": sum(r["status"] == 304 for r in results), "failed": failed, "catalog": releases}


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
