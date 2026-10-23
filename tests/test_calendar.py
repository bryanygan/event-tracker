"""Editorial calendar (P1-13): a loose plan of your own. Nothing posts, and it doesn't nag about how much is planned.
Invented artist names only."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, config, db, planner, posts
from tests.factory import tm_event
from tests.test_pipeline_data import ingest

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)  # Wed Oct 21, noon ET; the calendar starts Mon Oct 19
ARTISTS = {"Nova Vale": "Pop", "Kilo Moon": "Rock", "Iron Lark": "Rock", "Soft Arcade": "Rock", "Echo Bay": "Pop"}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    ingest(c, [tm_event(event_id=n.replace(" ", ""), name=n, genre=g, date="2026-11-08") for n, g in ARTISTS.items()])
    return c


def add_alert(conn, artist, kind="new_tour", fmt="new_tour", planned=None, post_by=None, series=None, created=NOW):
    facts = {"format": fmt, "artist": artist, "shows": [{"artist": artist, "date": "Sun, Nov 8", "venue": "Union Transfer"}]}
    cur = conn.execute(
        """INSERT INTO alerts (key, kind, title, score, facts, created_at, status, post_by, expires_at, planned_for, series)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (f"{kind}:{artist}:{planned}", kind, f"{artist} idea", 70, json.dumps(facts), created.isoformat(), "new",
         post_by and post_by.isoformat(), None, planned and planned.isoformat(), series))
    conn.commit()
    return cur.lastrowid


def at(day, hour=22):  # 22:00 UTC = 6 PM ET in October
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def day(cal, iso):
    return next(d for d in cal["days"] if d["date"] == iso)


def test_plans_posts_and_deadlines_land_on_their_local_day(conn):
    planned = add_alert(conn, "Nova Vale", planned=at(23))
    due = add_alert(conn, "Kilo Moon", kind="presale_soon", fmt="presale", post_by=at(24, 3))  # Fri 11 PM ET
    posted = add_alert(conn, "Iron Lark")
    posts.create_post(conn, alert_id=posted, posted_at=at(20).isoformat())
    cal = planner.calendar(conn, now=NOW)
    assert cal["start"] == "2026-10-19" and len(cal["days"]) == config.CALENDAR_DAYS
    fri = day(cal, "2026-10-23")
    assert [i["alert_id"] for i in fri["planned"]] == [planned]
    assert [i["alert_id"] for i in fri["due"]] == [due]          # by local day, not the UTC date (Oct 24)
    assert [p["alert_id"] for p in day(cal, "2026-10-20")["posted"]] == [posted]
    assert day(cal, "2026-10-20")["past"] and day(cal, "2026-10-21")["today"]
    assert cal["unplanned"][0]["alert_id"] == due                 # soonest deadline first
    assert planned not in [i["alert_id"] for i in cal["unplanned"]]


def test_busy_days_are_fine_and_only_late_plans_get_a_note(conn):
    for a in ("Nova Vale", "Kilo Moon", "Iron Lark", "Soft Arcade"):  # four on one day: a shortlist, not a problem
        add_alert(conn, a, kind="presale_soon", fmt="presale", planned=at(23))
    late = add_alert(conn, "Echo Bay", kind="presale_soon", fmt="presale", planned=at(26), post_by=at(25))
    cal = planner.calendar(conn, now=NOW)
    assert "warnings" not in cal
    fri = day(cal, "2026-10-23")
    assert len(fri["planned"]) == 4 and all(i["late"] is None for i in fri["planned"])
    [mon] = day(cal, "2026-10-26")["planned"]
    assert mon["alert_id"] == late and mon["late"] == "after its post-by time"
    [week] = [m for m in cal["mix"] if m["week"] == "2026-10-19"]  # for reference, no targets
    assert week["formats"] == {"presale": 4} and (week["lane"], week["music"]) == (1, 4)


def test_series_default_from_format_override_and_behind(conn):
    weekend = add_alert(conn, "Nova Vale", kind="weekend", fmt="weekend")
    pid = posts.create_post(conn, alert_id=weekend, posted_at=(NOW - timedelta(days=9)).isoformat())
    assert conn.execute("SELECT series FROM posts WHERE id=?", (pid,)).fetchone()[0] == "Philly this weekend"
    mine = add_alert(conn, "Kilo Moon", series="Deep cuts")
    cal = planner.calendar(conn, now=NOW)
    weekly = next(s for s in cal["series"] if s["name"] == "Philly this weekend")
    assert weekly["behind"] and weekly["cadence_days"] == 7
    assert not next(s for s in cal["series"] if s["name"] == "Stacked month")["behind"]  # never posted: not behind
    assert any(s["name"] == "Deep cuts" for s in cal["series"])
    assert next(i for i in cal["unplanned"] if i["alert_id"] == mine)["series"] == "Deep cuts"
    add_alert(conn, "Echo Bay", kind="weekend", fmt="weekend", planned=at(23))  # planning one clears it
    assert not next(s for s in planner.calendar(conn, now=NOW)["series"] if s["name"] == "Philly this weekend")["behind"]


def test_dismissed_and_posted_ideas_leave_the_plan(conn):
    gone = add_alert(conn, "Nova Vale", planned=at(23))
    conn.execute("UPDATE alerts SET status='dismissed' WHERE id=?", (gone,))
    done = add_alert(conn, "Kilo Moon", planned=at(23))
    posts.create_post(conn, alert_id=done, posted_at=at(23).isoformat())
    fri = day(planner.calendar(conn, now=NOW), "2026-10-23")
    assert fri["planned"] == [] and [p["alert_id"] for p in fri["posted"]] == [done]


def test_a_plan_changes_nothing_on_the_queue(conn):
    a = add_alert(conn, "Nova Vale")
    from tracker import opportunities
    before = next(i for i in opportunities.queue(conn, NOW) if i["alert_id"] == a)
    conn.execute("UPDATE alerts SET planned_for=? WHERE id=?", (at(30).isoformat(), a))  # after it goes stale
    after = next(i for i in opportunities.queue(conn, NOW) if i["alert_id"] == a)
    assert {k: v for k, v in after.items() if k != "planned_for"} == {k: v for k, v in before.items() if k != "planned_for"}


def test_api_plan_and_clear(tmp_path, monkeypatch):
    path = tmp_path / "c.db"
    c = db.connect(path)
    ingest(c, [tm_event(event_id="NV", name="Nova Vale", date=(datetime.now().date() + timedelta(days=20)).isoformat())])
    aid = add_alert(c, "Nova Vale")
    c.close()
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    client = TestClient(api.app)
    assert client.patch(f"/api/alerts/{aid}", json={"planned_for": "2026-10-23T18:00"}).status_code == 400  # which zone?
    assert client.patch(f"/api/alerts/{aid}", json={"planned_for": "2026-10-23T18:00:00-04:00",
                                                    "series": "  Deep cuts "}).status_code == 200
    with db.connect(path) as c2:
        row = c2.execute("SELECT planned_for, series FROM alerts WHERE id=?", (aid,)).fetchone()
    assert tuple(row) == ("2026-10-23T22:00:00+00:00", "Deep cuts")
    assert client.get("/api/calendar").status_code == 200
    assert client.get("/api/calendar", params={"start": "nope"}).status_code == 400
    client.patch(f"/api/alerts/{aid}", json={"planned_for": None, "series": ""})
    with db.connect(path) as c2:
        assert tuple(c2.execute("SELECT planned_for, series FROM alerts WHERE id=?", (aid,)).fetchone()) == (None, None)
