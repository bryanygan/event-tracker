"""Is every source still working? One line per source for the Runs tab, and the problems for the banner.

Reads only what the runs already record (runs, source_runs, news_sources): no extra requests. A source counts as
a problem when its latest read failed or found nothing, or when the scheduled run that reads it stopped running.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config

# Longest normal gap between runs: the 4:30 PM run to the next 6:30 AM one is 14 hours; news runs every 30 min.
PIPELINE_STALE = timedelta(hours=15)
NEWS_STALE = timedelta(hours=2)
NEWS_FAILURES = 2  # one failed read is usually a blip; the next check retries it


def _ago(iso: str | None, now: datetime) -> timedelta | None:
    return now - datetime.fromisoformat(iso) if iso else None


def _last_ok(conn, key: str) -> str | None:
    row = conn.execute("""SELECT MAX(r.started_at) FROM source_runs s JOIN runs r ON r.id = s.run_id
                          WHERE s.source = ? AND s.ok = 1 AND s.count > 0""", (key,)).fetchone()
    return row[0]


def _run_check(conn, name: str, source: str, stale: timedelta, now: datetime, what: str) -> dict:
    last = conn.execute("SELECT * FROM runs WHERE source = ? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1",
                        (source,)).fetchone()
    good = conn.execute("SELECT MAX(started_at) FROM runs WHERE source = ? AND ok = 1", (source,)).fetchone()[0]
    check = {"group": "Scheduled runs", "name": name, "last_ok": good,
             "found": last["events_fetched"] if last else None, "what": what, "problem": None}
    if not last:
        pass  # a fresh install: nothing to compare against yet
    elif not last["ok"]:
        check["problem"] = f"last run failed: {last['error']}"
    elif _ago(last["started_at"], now) > stale:
        check["problem"] = f"no run for {int(_ago(last['started_at'], now).total_seconds() // 3600)} hours"
    return check


def _source_checks(conn, group: str, sources: list[dict], what: str) -> list[dict]:
    latest = {r["source"]: r for r in conn.execute(
        """SELECT s.* FROM source_runs s
           WHERE s.run_id = (SELECT MAX(run_id) FROM source_runs s2 WHERE s2.source = s.source)""")}
    out = []
    for src in sources:
        r = latest.get(src["key"])
        problem = None
        if r is not None and not r["ok"]:
            problem = r["error"] or "read failed"
        elif r is not None and not r["count"]:
            problem = f"0 {what} found"
        out.append({"group": group, "name": src["name"], "last_ok": _last_ok(conn, src["key"]),
                    "found": r["count"] if r is not None else None, "what": what, "problem": problem})
    return out


def _news_checks(conn, now: datetime) -> list[dict]:
    out = []
    rows = conn.execute("SELECT * FROM news_sources WHERE enabled = 1 ORDER BY origin != 'config', name").fetchall()
    for r in (r for r in rows if r["origin"] == "config"):
        problem = None
        if r["failures"] >= NEWS_FAILURES:
            problem = f"{r['failures']} failed reads in a row: {r['last_error']}"
        elif r["last_ok_at"] is None and r["last_fetched_at"] is not None:
            problem = r["last_error"] or "never read"
        out.append({"group": "Music news", "name": r["name"], "last_ok": r["last_ok_at"], "found": None,
                    "what": "", "problem": problem})
    # Artists' own channels (M8) can number in the dozens: one summary line, naming only the failing ones
    channels = [r for r in rows if r["origin"] != "config"]
    if channels:
        failing = [r["name"] for r in channels if r["failures"] >= NEWS_FAILURES]
        out.append({"group": "Music news", "name": "Artists' own channels", "found": len(channels),
                    "last_ok": max((r["last_ok_at"] for r in channels if r["last_ok_at"]), default=None),
                    "what": "channels", "problem": f"failing: {', '.join(failing)}" if failing else None})
    return out


def checks(conn, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    return [
        _run_check(conn, "Events pipeline (Ticketmaster)", "ticketmaster", PIPELINE_STALE, now, "events"),
        _run_check(conn, "Music news check", "news", NEWS_STALE, now, "new articles"),
        *_source_checks(conn, "Venue calendars", config.VENUE_CALENDARS, "shows"),
        *_source_checks(conn, "Philly happenings", config.EXPERIENCE_SOURCES, "listings"),
        *_news_checks(conn, now),
    ]


def problems(conn, now: datetime | None = None) -> list[dict]:
    return [{"source": c["name"], "error": c["problem"]} for c in checks(conn, now) if c["problem"]]
