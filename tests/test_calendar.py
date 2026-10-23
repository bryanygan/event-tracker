"""Editorial calendar (P1-13): plans are yours, nothing posts, and the warnings come from stored data.
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
    assert "Thu, Oct 22" in cal["empty_days"] and "Fri, Oct 23" not in cal["empty_days"]
    assert "Tue, Oct 20" not in cal["empty_days"]                 # past days aren't gaps
    assert [i["alert_id"] for i in cal["unplanned"]] == [due, *[i["alert_id"] for i in cal["unplanned"][1:]]]
    assert planned not in [i["alert_id"] for i in cal["unplanned"]]


def test_warnings_for_crowded_days_repeats_and_late_plans(conn):
    for a in ("Nova Vale", "Kilo Moon", "Iron Lark"):
        add_alert(conn, a, planned=at(23))
    add_alert(conn, "Nova Vale", kind="presale_soon", fmt="presale", planned=at(26), post_by=at(25))
    cal = planner.calendar(conn, now=NOW)
    w = " | ".join(cal["warnings"])
    assert "Fri, Oct 23: 3 videos planned" in w
    assert "Nova Vale is in 2 videos close together (planned Fri, Oct 23, planned Mon, Oct 26)" in w
    assert "after its post-by time" in w


def test_a_plan_soon_after_a_post_about_the_same_artist_is_flagged(conn):
    old = add_alert(conn, "Echo Bay")
    posts.create_post(conn, alert_id=old, posted_at=at(20).isoformat())
    add_alert(conn, "Echo Bay", kind="presale_soon", fmt="presale", planned=at(24))
    assert any("Echo Bay is in 2 videos close together (posted Tue, Oct 20, planned Sat, Oct 24)" in w
               for w in planner.calendar(conn, now=NOW)["warnings"])


def test_week_balance_format_and_lane(conn):
    for i, a in enumerate(("Kilo Moon", "Iron Lark", "Soft Arcade", "Kilo Moon")):
        add_alert(conn, a, kind="presale_soon", fmt="presale", planned=at(22 + i, 14))
    cal = planner.calendar(conn, now=NOW)
    [week] = [b for b in cal["balance"] if b["week"] == "2026-10-19"]
    assert week["formats"] == {"presale": 4} and (week["lane"], week["music"]) == (0, 4)
    w = " | ".join(cal["warnings"])
    assert "4 presale videos; mix in another format" in w and "only 0 of 4 music videos are in your lane" in w


def test_series_default_from_format_override_and_overdue(conn):
    weekend = add_alert(conn, "Nova Vale", kind="weekend", fmt="weekend")
    pid = posts.create_post(conn, alert_id=weekend, posted_at=(NOW - timedelta(days=9)).isoformat())
    assert conn.execute("SELECT series FROM posts WHERE id=?", (pid,)).fetchone()[0] == "Philly this weekend"
    mine = add_alert(conn, "Kilo Moon", series="Deep cuts")
    cal = planner.calendar(conn, now=NOW)
    weekly = next(s for s in cal["series"] if s["name"] == "Philly this weekend")
    assert weekly["overdue"] and weekly["cadence_days"] == 7
    assert any("Philly this weekend: last posted" in w for w in cal["warnings"])
    assert any(s["name"] == "Deep cuts" for s in cal["series"])
    assert next(i for i in cal["unplanned"] if i["alert_id"] == mine)["series"] == "Deep cuts"
    add_alert(conn, "Echo Bay", kind="weekend", fmt="weekend", planned=at(23))  # planning one clears it
    assert not next(s for s in planner.calendar(conn, now=NOW)["series"] if s["name"] == "Philly this weekend")["overdue"]


def test_dismissed_and_posted_ideas_leave_the_plan(conn):
    gone = add_alert(conn, "Nova Vale", planned=at(23))
    conn.execute("UPDATE alerts SET status='dismissed' WHERE id=?", (gone,))
    done = add_alert(conn, "Kilo Moon", planned=at(23))
    posts.create_post(conn, alert_id=done, posted_at=at(23).isoformat())
    fri = day(planner.calendar(conn, now=NOW), "2026-10-23")
    assert fri["planned"] == [] and [p["alert_id"] for p in fri["posted"]] == [done]


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
    [item] = [i for i in client.get("/api/queue").json()["items"] if i["alert_id"] == aid]
    assert item["planned_for"] == "2026-10-23T22:00:00+00:00" and item["series"] == "Deep cuts"
    assert client.get("/api/calendar").status_code == 200
    assert client.get("/api/calendar", params={"start": "nope"}).status_code == 400
    client.patch(f"/api/alerts/{aid}", json={"planned_for": None, "series": ""})
    with db.connect(path) as c2:
        assert tuple(c2.execute("SELECT planned_for, series FROM alerts WHERE id=?", (aid,)).fetchone()) == (None, None)
