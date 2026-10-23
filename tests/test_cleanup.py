"""Deezer's genre as a check on Ticketmaster's, the Claude-sized draft limit, and redrafting old local drafts.
Invented artist names only."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import responses

from tracker import config, db, drafts, llm
from tracker.fetchers import deezer
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event
from tests.test_pipeline_data import ingest
from tests.test_roundups_genres import add_cal, by_id, cal_event


@pytest.fixture
def conn():
    return db.connect(":memory:")


def test_release_genre_needs_enough_agreeing_releases():
    assert deezer.release_genre(["Pop"] * 8 + ["Electro"] * 2) == "Pop"
    assert deezer.release_genre(["Electro", "Dance", "Pop"]) == "Dance/Electronic"  # both map to one TM genre
    assert deezer.release_genre(["Pop", "Pop"]) is None                             # too few releases
    assert deezer.release_genre(["Pop", "Rock", "Pop", "Rock"]) is None             # no clear majority
    assert deezer.release_genre(["Films/Games", None, "Pop", "Pop", "Pop"]) == "Pop"  # unmapped ones don't count


def test_deezer_replaces_ticketmaster_only_across_families(conn):
    ingest(conn, [tm_event(event_id="a", name="Nova Vale", genre="Rock"),
                  tm_event(event_id="b", name="Kilo Moon", genre="Hip-Hop/Rap"),
                  tm_event(event_id="c", name="Iron Lark", genre="Dance/Electronic"),
                  tm_event(event_id="d", name="Soft Arcade", genre="Pop")])
    for name, genre in [("Nova Vale", "Pop"), ("Kilo Moon", "Pop"), ("Iron Lark", "Rock"), ("Soft Arcade", "Alternative")]:
        conn.execute("UPDATE attractions SET deezer_genre=? WHERE name=?", (genre, name))
    e = by_id(conn)
    assert (e["a"]["genre"], e["a"]["subgenre"], e["a"]["genre_source"], e["a"]["ticketmaster_genre"]) == \
        ("Pop", None, "deezer", "Rock")
    assert e["a"]["niche"]  # now counts toward your lane
    assert (e["b"]["genre"], e["b"]["genre_source"]) == ("Hip-Hop/Rap", "listing")  # same family: TM stays
    assert (e["c"]["genre"], e["c"]["ticketmaster_genre"]) == ("Rock", "Dance/Electronic")
    assert not e["c"]["niche"]
    assert (e["d"]["genre"], e["d"]["niche"]) == ("Pop", True)  # Deezer's "Alternative" is often indie pop


def test_deezer_fills_shows_with_no_genre_but_not_non_music(conn):
    raw = tm_event(event_id="play", name="Kilo Moon", genre="Theatre")
    raw["classifications"][0]["segment"]["name"] = "Arts & Theatre"
    add_cal(conn, tm.normalize(raw), cal_event("Nova Vale"))
    conn.execute("UPDATE attractions SET deezer_genre='Pop'")
    e = by_id(conn)
    assert (e["cal:sr:nova-vale-2026-10-24"]["genre"], e["cal:sr:nova-vale-2026-10-24"]["genre_source"]) == ("Pop", "deezer")
    assert e["play"]["genre"] == "Theatre"  # a play's headliner may share a musician's name


@responses.activate
def test_enrich_stores_fans_and_release_genre(conn):
    ingest(conn, [tm_event(event_id="a", name="Nova Vale", genre="Rock")])
    responses.get(deezer.SEARCH_URL, json={"data": [{"id": 7, "name": "Nova Vale", "nb_fan": 50000}]})
    responses.get(deezer.ALBUMS_URL.format(7), json={"data": [{"genre_id": 132}] * 4 + [{"genre_id": 106}]})
    responses.get(deezer.GENRES_URL, json={"data": [{"id": 132, "name": "Pop"}, {"id": 106, "name": "Electro"}]})
    assert deezer.enrich_headliners(conn, deezer.DeezerClient(min_interval=0)) == 1
    row = conn.execute("SELECT fans, deezer_genre, deezer_genre_checked_at FROM attractions").fetchone()
    assert row["fans"] == 50000 and row["deezer_genre"] == "Pop" and row["deezer_genre_checked_at"]
    # checked: the next run doesn't ask again
    assert deezer.enrich_headliners(conn, deezer.DeezerClient(min_interval=0)) == 0


def test_older_lookups_without_a_genre_are_backfilled(conn):
    ingest(conn, [tm_event(event_id="a", name="Nova Vale")])
    conn.execute("UPDATE attractions SET popularity_checked_at=?", (datetime.now(timezone.utc).isoformat(),))
    asked = []

    class Fake:
        calls = 0
        def find_artist(self, name):
            asked.append(name)
            return None
    deezer.enrich_headliners(conn, Fake())
    assert asked == ["Nova Vale"]


def _alert(conn, alert_id, model, expires="2099-01-01T00:00:00+00:00", score=50):
    conn.execute("INSERT INTO alerts (id, key, kind, title, score, facts, created_at, status, expires_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)",
                 (alert_id, f"k{alert_id}", "new_tour", "t", score, json.dumps({"format": "new_tour", "shows": []}),
                  "2026-10-01T00:00:00+00:00", "drafted", expires))
    conn.execute("INSERT INTO drafts (alert_id, format, model, hook, script, caption, hashtags, onscreen, warnings, "
                 "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (alert_id, "new_tour", model, "h", "s", "c", "", "[]", "[]", "2026-10-01T00:00:00+00:00"))


def test_redraft_picks_live_alerts_whose_newest_draft_is_local(conn, monkeypatch):
    _alert(conn, 1, "qwen2.5:3b", score=60)
    _alert(conn, 2, "template + qwen2.5:3b", score=90)
    _alert(conn, 3, "claude-haiku-5-5")
    _alert(conn, 4, "qwen2.5:3b", expires="2020-01-01T00:00:00+00:00")  # stale idea
    _alert(conn, 5, "template")  # no model involved
    assert drafts.local_drafted(conn) == [2, 1]
    monkeypatch.setattr(llm, "uses_claude", lambda: True)
    done = []
    monkeypatch.setattr(drafts, "draft_alert", lambda c, alert_id: done.append(alert_id) or alert_id)
    assert drafts.redraft_local(conn) == [2, 1]
    monkeypatch.setattr(config, "CLAUDE_DAILY_CAP", 1)  # near the cap it stops instead of drafting with Ollama
    assert drafts.redraft_local(conn) == []


def test_redraft_needs_claude(conn, monkeypatch):
    monkeypatch.setattr(llm, "uses_claude", lambda: False)
    with pytest.raises(SystemExit):
        drafts.redraft_local(conn)


def test_draft_limit_is_larger_with_claude(conn, monkeypatch):
    for i in range(25):
        conn.execute("INSERT INTO alerts (key, kind, title, score, facts, created_at) VALUES (?,?,?,?,?,?)",
                     (f"k{i}", "new_tour", "t", i, "{}", "2026-10-01T00:00:00+00:00"))
    done = []
    monkeypatch.setattr(drafts, "draft_alert", lambda c, alert_id: done.append(alert_id) or alert_id)
    monkeypatch.setattr(llm, "uses_claude", lambda: True)
    assert len(drafts.draft_pending(conn)) == drafts.MAX_CLAUDE_DRAFTS_PER_RUN
    monkeypatch.setattr(llm, "uses_claude", lambda: False)
    assert len(drafts.draft_pending(conn)) == drafts.MAX_DRAFTS_PER_RUN
