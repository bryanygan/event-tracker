"""Alerts raised earlier get their facts refreshed; drafts keep what they were written from."""
import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tracker import api, db, detectors, posts
from tests.factory import tm_event
from tests.test_pipeline_data import ingest

WED = date(2026, 10, 21)


@pytest.fixture
def conn():
    return db.connect(":memory:")


def add_draft(conn, alert_id=1):
    facts = conn.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()[0]
    conn.execute("""INSERT INTO drafts (alert_id, format, hook, script, caption, hashtags, onscreen, warnings, created_at, facts)
                    VALUES (?, 'weekend', 'my edited hook', 's', 'c', '#philly', '[]', '[]', 'now', ?)""", (alert_id, facts))
    conn.commit()


def weekend(conn):
    detectors.save_alerts(conn, detectors.detect_weekend(conn, WED))
    return conn.execute("SELECT * FROM alerts WHERE key='weekend:2026-10-23'").fetchone()


def test_weekend_roundup_refreshes_when_a_show_is_added_or_cancelled(conn):
    raws = [tm_event(event_id=f"r{i}", name=f"Band {i}", date="2026-10-24", genre=g)
            for i, g in enumerate(["Rock", "Pop", "R&B"])]
    ingest(conn, raws)
    first = weekend(conn)
    add_draft(conn, first["id"])

    ingest(conn, raws + [tm_event(event_id="new", name="Late Add", date="2026-10-25", genre="Hip-Hop/Rap")])
    second = weekend(conn)
    assert second["id"] == first["id"] and second["updated_at"]
    assert "Late Add" in second["facts"] and "Late Add" not in first["facts"]
    [hist] = conn.execute("SELECT * FROM alert_history").fetchall()
    assert hist["facts"] == first["facts"]
    draft = conn.execute("SELECT * FROM drafts").fetchone()
    assert draft["hook"] == "my edited hook" and draft["facts"] == first["facts"]   # draft untouched
    assert detectors.facts_diff(json.loads(draft["facts"]), json.loads(second["facts"])) == ["Added: Late Add (Sun, Oct 25)"]

    weekend(conn)  # same data again: idempotent, no new history row
    assert conn.execute("SELECT COUNT(*) FROM alert_history").fetchone()[0] == 1


def a(facts, title="t", score=50):
    return {"key": "k", "kind": "presale_soon", "title": title, "score": score, "facts": facts}


def test_volatile_facts_alone_do_not_refresh(conn):
    detectors.save_alerts(conn, [a({"sale_opens": "Thu", "hours_until_open": 40, "your_plays": 3})])
    detectors.save_alerts(conn, [a({"sale_opens": "Thu", "hours_until_open": 28, "your_plays": 4})])
    assert conn.execute("SELECT updated_at FROM alerts").fetchone()[0] is None


def test_a_fact_that_did_not_exist_yet_is_not_a_change(conn):
    """Regression (real data): alerts raised before setlist.fm history have no `history` key; now it's None."""
    detectors.save_alerts(conn, [a({"sale_opens": "Thu"})])
    detectors.save_alerts(conn, [a({"sale_opens": "Thu", "history": None})])
    assert conn.execute("SELECT updated_at FROM alerts").fetchone()[0] is None
    detectors.save_alerts(conn, [a({"sale_opens": "Thu", "history": {"last_philly_show": "Aug 3, 2019"}})])
    old, new = (json.loads(r[0]) for r in conn.execute("SELECT facts FROM alert_history UNION ALL SELECT facts FROM alerts"))
    assert detectors.facts_diff(old, new) == ["Last Philly show (setlist.fm): — → Aug 3, 2019"]


def test_dismissed_and_posted_alerts_are_left_alone(conn):
    detectors.save_alerts(conn, [a({"sale_opens": "Thu"})])
    conn.execute("UPDATE alerts SET status='dismissed'")
    detectors.save_alerts(conn, [a({"sale_opens": "Fri"})])
    assert "Thu" in conn.execute("SELECT facts FROM alerts").fetchone()[0]

    conn.execute("UPDATE alerts SET status='drafted'")
    posts.create_post(conn, alert_id=1, posted_at="2026-10-21T20:00:00Z")
    detectors.save_alerts(conn, [a({"sale_opens": "Fri"})])
    assert "Thu" in conn.execute("SELECT facts FROM alerts").fetchone()[0]


def test_facts_diff_lines():
    show = {"artist": "Nova Vale", "date": "Sat, Oct 24", "venue": "Union Transfer", "price": "$40"}
    old = {"sale_opens": "Thu, Oct 22 at 10:00 AM ET", "shows": [show],
           "all_sales": [{"name": "Artist presale", "opens": "Thu"}], "hours_until_open": 5}
    new = {"sale_opens": "Fri, Oct 23 at 10:00 AM ET", "shows": [{**show, "price": "$45", "status": "cancelled"}],
           "all_sales": [{"name": "General public on-sale", "opens": "Fri"}], "hours_until_open": 1}
    assert detectors.facts_diff(old, new) == [
        "Nova Vale (Sat, Oct 24): status — → cancelled",
        "Nova Vale (Sat, Oct 24): price $40 → $45",
        "New sale: General public on-sale, Fri",
        "Sale no longer listed: Artist presale, Thu",
        "sale_opens: Thu, Oct 22 at 10:00 AM ET → Fri, Oct 23 at 10:00 AM ET",
    ]
    assert detectors.facts_diff(old, {**old, "hours_until_open": 2}) == []
    one = {"show": show}
    assert detectors.facts_diff(one, {"show": {**show, "date": "Sun, Oct 25"}}) == [
        "Added: Nova Vale (Sun, Oct 25)", "Removed: Nova Vale (Sat, Oct 24)"]


def test_old_database_drafts_get_their_facts_backfilled(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)  # the pre-refresh schema: no alerts.updated_at, no drafts.facts
    raw.executescript("""
        CREATE TABLE alerts (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
            score REAL DEFAULT 0, facts TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new');
        CREATE TABLE drafts (id INTEGER PRIMARY KEY, alert_id INTEGER REFERENCES alerts(id), format TEXT NOT NULL,
            model TEXT, hook TEXT, script TEXT, caption TEXT, hashtags TEXT, onscreen TEXT, warnings TEXT,
            created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', notes TEXT);
        INSERT INTO alerts (key, kind, title, facts, created_at, status) VALUES ('k', 'weekend', 't', '{"x": 1}', 'then', 'drafted');
        INSERT INTO drafts (alert_id, format, created_at, status) VALUES (1, 'weekend', 'then', 'approved');
    """)
    raw.close()
    c = db.connect(path)
    d = c.execute("SELECT facts, status FROM drafts").fetchone()
    assert (d["facts"], d["status"]) == ('{"x": 1}', "approved")
    assert c.execute("SELECT updated_at FROM alerts").fetchone()[0] is None


def test_api_reports_changes_per_draft(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    detectors.save_alerts(c, [a({"sale_opens": "Thu"})])
    add_draft(c)
    detectors.save_alerts(c, [a({"sale_opens": "Fri"}, title="moved")])
    c.close()
    [alert] = TestClient(api.app).get("/api/alerts").json()
    assert alert["title"] == "moved" and alert["updated_at"]
    assert alert["drafts"][0]["facts_changes"] == ["sale_opens: Thu → Fri"]
    assert "facts" not in alert["drafts"][0]
