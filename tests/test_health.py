"""Source health (tracker/health.py): each source's latest read, scheduled runs that stopped, news feeds that
keep failing. Read from what the runs recorded; nothing is fetched."""
from datetime import datetime, timezone

import pytest

from tracker import config, db, health

NOW = datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VENUE_CALENDARS", [{"key": "ua", "name": "Underground Arts"},
                                                    {"key": "mb", "name": "MilkBoy"}])
    monkeypatch.setattr(config, "EXPERIENCE_SOURCES", [{"key": "ccd", "name": "Center City District"}])
    return db.connect(tmp_path / "h.db")


def _run(conn, source, started, ok=1, error=None, fetched=500):
    cur = conn.execute("INSERT INTO runs (started_at, finished_at, source, events_fetched, api_calls, ok, error) "
                       "VALUES (?,?,?,?,?,?,?)", (started, started, source, fetched, 4, ok, error))
    return cur.lastrowid


def _src(conn, run_id, key, ok=1, count=10, error=None):
    conn.execute("INSERT INTO source_runs VALUES (?,?,?,?,?)", (run_id, key, ok, count, error))


def _news(conn, key, name, origin="config", failures=0, error=None, last_ok="2026-10-24T20:40:00+00:00"):
    conn.execute("""INSERT INTO news_sources (key, name, url, origin, failures, last_error, last_ok_at, last_fetched_at)
                    VALUES (?,?,?,?,?,?,?,?)""", (key, name, "https://example.com/" + key, origin, failures, error,
                                                  last_ok, "2026-10-24T20:40:00+00:00"))


def test_all_working(conn):
    old = _run(conn, "ticketmaster", "2026-10-24T10:30:00+00:00")
    _src(conn, old, "ua"), _src(conn, old, "mb"), _src(conn, old, "ccd")
    new = _run(conn, "ticketmaster", "2026-10-24T20:30:00+00:00")
    _src(conn, new, "ua"), _src(conn, new, "mb"), _src(conn, new, "ccd", count=14)
    _run(conn, "news", "2026-10-24T20:40:00+00:00", fetched=13)
    _news(conn, "pitchfork", "Pitchfork news")
    checks = health.checks(conn, NOW)
    assert [c["problem"] for c in checks] == [None] * 6
    ccd = next(c for c in checks if c["name"] == "Center City District")
    assert ccd["found"] == 14 and ccd["what"] == "listings" and ccd["last_ok"] == "2026-10-24T20:30:00+00:00"
    assert health.problems(conn, NOW) == []


def test_failed_or_empty_source_shows_when_it_last_worked(conn):
    good = _run(conn, "ticketmaster", "2026-10-24T10:30:00+00:00")
    _src(conn, good, "ua"), _src(conn, good, "mb")
    bad = _run(conn, "ticketmaster", "2026-10-24T20:30:00+00:00")
    _src(conn, bad, "ua", ok=0, count=None, error="fetch failed: timeout")
    _src(conn, bad, "mb", count=0)
    assert health.problems(conn, NOW) == [{"source": "Underground Arts", "error": "fetch failed: timeout"},
                                          {"source": "MilkBoy", "error": "0 shows found"}]
    ua = next(c for c in health.checks(conn, NOW) if c["name"] == "Underground Arts")
    assert ua["last_ok"] == "2026-10-24T10:30:00+00:00"


def test_stopped_or_failed_scheduled_runs(conn):
    _run(conn, "ticketmaster", "2026-10-23T20:30:00+00:00")  # the 6:30 AM run never happened
    _run(conn, "news", "2026-10-24T20:40:00+00:00", ok=0, error="failed: Pitchfork news (timeout)")
    probs = {p["source"]: p["error"] for p in health.problems(conn, NOW)}
    assert probs["Events pipeline (Ticketmaster)"] == "no run for 24 hours"
    assert probs["Music news check"].startswith("last run failed")


def test_fresh_install_isnt_a_problem(conn):
    assert health.problems(conn, NOW) == []


def test_news_feed_needs_two_failures_and_artist_channels_are_one_line(conn):
    _news(conn, "a", "Feed A", failures=1, error="timeout")  # a blip: the next check retries it
    _news(conn, "b", "Feed B", failures=3, error="HTTP 500")
    _news(conn, "yt-x", "Nova Vale (YouTube)", origin="musicbrainz")
    _news(conn, "yt-y", "Kilo Moon (YouTube)", origin="musicbrainz", failures=2, error="channel not found")
    probs = health.problems(conn, NOW)
    assert probs == [{"source": "Feed B", "error": "3 failed reads in a row: HTTP 500"},
                     {"source": "Artists' own channels", "error": "failing: Kilo Moon (YouTube)"}]
    line = next(c for c in health.checks(conn, NOW) if c["name"] == "Artists' own channels")
    assert line["found"] == 2 and line["what"] == "channels"
