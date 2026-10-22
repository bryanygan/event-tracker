"""Niche boost from listening history (a fake plays.db; the real one is never read in tests)."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from tracker import config, db, detectors, listening, llm, store
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event


@pytest.fixture
def plays_db(tmp_path, monkeypatch):
    path = tmp_path / "plays.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE plays (artist TEXT, played_at TEXT)")
    con.execute("CREATE TABLE artist_genres (artist_key TEXT PRIMARY KEY, primary_bucket TEXT)")
    recent, old = (date.today() - timedelta(days=30)).isoformat(), (date.today() - timedelta(days=1000)).isoformat()
    rows = [("O’Fakename Artist", recent)] * 250 + [("casedupe", recent)] * 20 + [("Casedupe", recent)] * 20
    rows += [("Old Fave", old)] * 500 + [("Tiny Indie", recent)] * 12
    con.executemany("INSERT INTO plays VALUES (?,?)", rows)
    con.executemany("INSERT INTO artist_genres VALUES (?,?)", [("tiny indie", "electronic"), ("# rock guy", "rock")])
    con.commit(); con.close()
    monkeypatch.setattr(config, "LISTENING_DB", str(path))
    return path


def test_load_merges_spellings_and_respects_window(plays_db):
    t = listening.load()
    assert t.plays_for("O'Fakename Artist") == 250            # curly vs straight apostrophe
    assert t.plays_for("CASEDUPE") == 40                       # "casedupe" + "Casedupe"
    assert t.plays_for("Old Fave") == 0                     # outside the 2-year window
    assert t.plays_for("Nobody", "Tiny Indie") == 12        # best act on the bill
    assert t.bucket_for("Tiny Indie") == "electronic"


def test_missing_db_is_harmless(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LISTENING_DB", str(tmp_path / "nope.db"))
    assert listening.load().plays == {}


def test_bonus_tiers_and_niche():
    assert [listening.plays_bonus(n) for n in (500, 60, 10, 9)] == [20, 12, 6, 0]
    assert listening.in_niche("Hip-Hop/Rap", None, None) and listening.in_niche(None, None, "rage")
    assert not listening.in_niche("Country", None, "rock")


def _ingest(conn, raws):
    run = store.start_run(conn, "ticketmaster")
    store.upsert_events(conn, run, [tm.normalize(r) for r in raws], baseline=False)
    return run


def test_scores_get_taste_bonus(plays_db, tmp_path):
    from tracker.scoring import upcoming_events
    conn = db.connect(tmp_path / "s.db")
    _ingest(conn, [tm_event(event_id="pb", name="O'Fakename Artist", venue="Brooklyn Bowl Philadelphia",
                            genre="Hip-Hop/Rap", upcoming=20, date="2099-01-01"),
                   tm_event(event_id="x", name="Stranger", venue="Brooklyn Bowl Philadelphia", genre="Country",
                            upcoming=20, date="2099-01-01")])
    by_id = {e["id"]: e for e in upcoming_events(conn, "2026-10-21")}
    assert by_id["pb"]["my_plays"] == 250 and by_id["pb"]["taste_bonus"] == 25   # 20 plays-bonus + 5 genre
    assert by_id["pb"]["score"] == by_id["x"]["score"] + 25


def test_rotation_artist_alerts_even_when_small(plays_db, tmp_path):
    conn = db.connect(tmp_path / "r.db")
    store.upsert_events(conn, store.start_run(conn, "t"), [tm.normalize(tm_event(event_id="old", name="Other"))],
                        baseline=True)
    run = _ingest(conn, [tm_event(event_id="ti", name="Tiny Indie", venue="Some Bar", upcoming=1, date="2099-01-01"),
                         tm_event(event_id="sl", name="casedupe", venue="Some Bar", upcoming=1, date="2099-01-01",
                                  presales=[("Artist Presale", "2026-10-22T14:00:00Z", None)])])
    new = detectors.detect_new_tours(conn, run, "2026-10-21")
    titles = sorted(a["title"] for a in new)
    assert len(new) == 1 and titles[0].startswith("🎧 ")       # casedupe (40 plays) alerts; Tiny Indie (12) doesn't
    assert new[0]["facts"]["your_plays"] == 40
    sales = detectors.detect_sales_soon(conn, datetime(2026, 10, 21, 16, tzinfo=timezone.utc), "2026-10-21")
    assert [a["facts"]["artist"] for a in sales] == ["casedupe"]


def test_plays_never_reach_the_llm_prompt():
    prompt = llm.build_prompt({"format": "new_tour", "artist": "X", "your_plays": 1348, "shows": []})
    assert "1348" not in prompt and "your_plays" not in prompt
