"""Recaps (tracker/recaps.py): many broad roundups, from the data only, on their own tab."""
import json
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, db, detectors, drafts, llm, opportunities, recaps, store
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event

NOW = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)  # Tuesday of Thanksgiving week


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "r.db")
    yield c
    c.close()


def load(conn, raws):
    run = store.start_run(conn, "tm")
    store.upsert_events(conn, run, [tm.normalize(r) for r in raws], baseline=True)
    store.finish_run(conn, run, fetched=len(raws), api_calls=0, ok=True)


def by_type(out, rtype):
    return [r for r in out if r["facts"]["recap_type"] == rtype]


def test_windows_are_dated_and_never_repeat_the_same_dates():
    wins = recaps.windows(date(2026, 11, 24))
    when = [w.when for w in wins]
    assert "Thanksgiving week (Nov 23–29)" in when and "the week of Nov 23" not in when  # same dates: one window
    assert "Thanksgiving weekend (Nov 27–29)" in when and "the weekend of Nov 27–29" not in when
    assert "the week of Nov 30" in when and "the weekend of Dec 4–6" in when
    assert "in December" in when and "December 1–15" in when and "December 16–31" in when
    assert "November 16–30" not in when  # already started: a stale title
    assert "New Year's Eve" in when and "Christmas week (Dec 21–27)" in when
    assert len({(w.start, w.end) for w in wins}) == len(wins)
    thanks = next(w for w in wins if w.key.startswith("thanksgiving_week"))
    assert thanks.start == date(2026, 11, 24)  # clipped to today


def test_genre_runs_of_nights_and_after_the_show(conn):
    load(conn, [
        # three hip-hop acts the first week of December, one of them two nights running
        tm_event(name="Nova Vale", genre="Hip-Hop/Rap", date="2026-12-01", venue="The Fillmore Philadelphia"),
        tm_event(name="Nova Vale", genre="Hip-Hop/Rap", date="2026-12-02", venue="The Fillmore Philadelphia"),
        tm_event(name="Kilo Moon", genre="Hip-Hop/Rap", date="2026-12-03", venue="Union Transfer"),
        tm_event(name="Echo Bay", genre="Hip-Hop/Rap", date="2026-12-05", venue="Franklin Music Hall"),
        # a monthly club night is a series, not a run of nights
        tm_event(name="Emo Night", genre="Rock", date="2026-12-01", venue="Kung Fu Necktie"),
        tm_event(name="Emo Night", genre="Rock", date="2027-01-05", venue="Kung Fu Necktie"),
        # an arena show, then two late shows elsewhere that night, one named an afterparty
        tm_event(name="Big Star", genre="Pop", date="2026-12-04", time="19:30:00", venue="Xfinity Mobile Arena",
                 upcoming=200),
        tm_event(name="Big Star Afterparty Hosted By DJ Two", artist="DJ Two", genre="Dance/Electronic",
                 date="2026-12-04", time="22:30:00", venue="NOTO"),
    ])
    out = recaps.detect_all(conn, NOW)
    assert len({r["key"] for r in out}) == len(out)
    hiphop = [r for r in by_type(out, "genre") if r["facts"]["genre"] == "Hip-Hop" and r["facts"]["when"] == "the week of Nov 30"]
    [r] = hiphop
    assert r["kind"] == "recap" and r["title"] == "Hip-Hop in Philly the week of Nov 30 (3 shows)"
    assert [s["artist"] for s in r["facts"]["shows"]] == ["Nova Vale", "Kilo Moon", "Echo Bay"]  # one line per act
    assert set(r["facts"]) <= {"format", "recap_type", "theme", "when", "show_count", "shows", "genre", "picks",
                               "pick_count"}  # shows and words only: no fan counts or plays

    [run] = [r for r in by_type(out, "multi_night")]
    assert run["facts"]["artist"] == "Nova Vale" and run["facts"]["night_count"] == 2
    assert run["title"] == "Nova Vale: 2 nights in Philly at The Fillmore Philadelphia (Dec 1–2)"

    [after] = by_type(out, "afterparty")
    assert after["facts"]["headliner"] == "Big Star" and after["facts"]["named_afterparties"]
    assert after["facts"]["concert"]["venue"] == "Xfinity Mobile Arena"
    assert after["event_ids"][0] != after["event_ids"][1]  # the concert, then the late show


def test_a_recap_drafts_from_its_own_guide_and_is_checked_like_any_other(conn):
    load(conn, [tm_event(name=n, genre="Rock", date=d, venue=v, price=(15.0, 20.0)) for n, d, v in (
        ("Glass Hours", "2026-12-01", "Johnny Brenda's"), ("Static Bloom", "2026-12-02", "PhilaMOCA"),
        ("Iron Lark", "2026-12-03", "Kung Fu Necktie"), ("Echo Bay", "2026-12-04", "MilkBoy Philadelphia"))])
    out = recaps.detect_all(conn, NOW)
    cheap = next(r for r in by_type(out, "cheap") if r["facts"]["when"] == "the week of Nov 30")
    facts = cheap["facts"]
    assert "cheapest listed ticket is under $25" in llm.build_prompt(facts)
    assert llm.onscreen_lines(facts)[0] == "SHOWS UNDER $25 · the week of Nov 30"
    assert llm.onscreen_lines(facts)[1] == "Tue, Dec 1 · Glass Hours · Johnny Brenda's · $15–$20"
    draft = {"hook": "4 shows under $25 in Philly the week of Nov 30",
             "script": "Glass Hours at Johnny Brenda's, Tue, Dec 1, $15–$20.", "caption": "Save this."}
    assert llm.check_facts(draft, facts) == []  # the theme's "under $25" is in the facts
    angles = recaps.angles_for(facts)
    assert {a["key"] for a in angles} >= {"news", "fan"} and all(len(a["hook"].split()) <= 12 for a in angles)


def test_recaps_have_their_own_tab_and_only_the_best_in_your_lane_join_what_to_post(conn, monkeypatch, tmp_path):
    load(conn, [tm_event(name=f"Act {i}", genre="Hip-Hop/Rap", date=f"2026-12-0{i}", venue="Union Transfer")
                for i in range(1, 5)] +
               [tm_event(name=f"Band {i}", genre="Country", date=f"2026-12-0{i}", venue="The Met") for i in range(1, 5)])
    detectors.save_alerts(conn, recaps.detect_all(conn, NOW))
    items = opportunities.queue(conn, NOW)
    mine = [i for i in items if i["kind"] == "recap" and i["on_queue"]]
    assert mine and all(i["lane"] == "lane" for i in mine) and len(mine) <= 6
    assert len({i["recap_type"] for i in mine}) == len(mine)  # one of each kind, for variety
    assert any(i["kind"] == "recap" and not i["on_queue"] for i in items)  # the rest: Recaps tab only
    country = next(i for i in items if "Country" in i["title"])
    assert country["recap_type"] == "genre" and country["lane"] == "other"
    # never drafted automatically: there are far too many
    monkeypatch.setattr(drafts, "_draft_all", lambda c, ids, claude_only=False: ids)
    assert drafts.draft_pending(conn, limit=50) == []

    monkeypatch.setattr(api, "conn", lambda: db.connect(tmp_path / "r.db"))
    cl = TestClient(api.app)
    assert cl.get("/api/alerts").json() == []  # Alerts & drafts: no recaps
    ids = [a["id"] for a in cl.get("/api/alerts", params={"kind": "recap"}).json()]
    assert ids and [a["id"] for a in cl.get("/api/alerts", params={"ids": f"{ids[0]},{ids[1]}"}).json()] == ids[:2]
    tab = cl.get("/api/queue", params={"section": "recaps"}).json()
    assert {i["kind"] for i in tab["items"]} == {"recap"} and tab["recap_types"]["multi_night"] == "Multiple nights"
    main = cl.get("/api/queue").json()["items"]
    assert all(i["on_queue"] for i in main)
    assert cl.get("/api/queue", params={"section": "nope"}).status_code == 400


def test_untouched_expired_recaps_are_pruned_and_touched_ones_kept(conn):
    for key, note in (("recap:genre:a", None), ("recap:genre:b", "film this")):
        conn.execute("""INSERT INTO alerts (key, kind, title, score, facts, created_at, expires_at, note)
                        VALUES (?, 'recap', 't', 1, ?, '2026-10-01T00:00:00+00:00', '2026-10-05T00:00:00+00:00', ?)""",
                     (key, json.dumps({"format": "recap"}), note))
    conn.commit()
    assert recaps.prune(conn, now="2026-11-24T00:00:00+00:00") == 1
    assert [r[0] for r in conn.execute("SELECT key FROM alerts")] == ["recap:genre:b"]
