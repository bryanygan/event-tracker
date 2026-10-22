import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, db, detectors, posts

POSTED = "2026-10-20T23:00:00+00:00"   # 7 PM ET on Tue Oct 20
T0 = datetime.fromisoformat(POSTED)


def at(hours: float) -> str:
    return (T0 + timedelta(hours=hours)).isoformat()


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    detectors.save_alerts(c, [{"key": "sale:x", "kind": "presale_soon", "title": "Tickets for Nova Vale open Thu",
                               "score": 60, "facts": {"format": "presale", "artist": "Nova Vale"}}])
    c.execute("""INSERT INTO drafts (alert_id, format, hook, script, caption, hashtags, onscreen, warnings, created_at)
                 VALUES (1, 'presale', 'Nova Vale tickets Thursday', 's', 'c', '#philly', '[]', '[]', 'now')""")
    c.commit()
    return c


def test_post_from_draft_copies_alert_context(conn):
    pid = posts.create_post(conn, draft_id=1, posted_at=POSTED, video_style="slideshow")
    [p] = posts.list_posts(conn, now=T0)
    assert p["id"] == pid and p["alert_id"] == 1
    assert (p["title"], p["format"], p["hook"]) == ("Tickets for Nova Vale open Thu", "presale", "Nova Vale tickets Thursday")
    assert p["facts_snapshot"] == {"format": "presale", "artist": "Nova Vale"}


def test_facts_snapshot_is_frozen_at_posting_time(conn):
    posts.create_post(conn, alert_id=1, posted_at=POSTED)
    conn.execute("UPDATE alerts SET facts=? WHERE id=1", (json.dumps({"format": "presale", "artist": "Changed"}),))
    assert posts.list_posts(conn, now=T0)[0]["facts_snapshot"]["artist"] == "Nova Vale"


def test_posted_at_needs_a_timezone_and_is_stored_utc(conn):
    with pytest.raises(posts.PostError):
        posts.create_post(conn, title="x", posted_at="2026-10-20T19:00")
    posts.create_post(conn, title="x", posted_at="2026-10-20T19:00:00-04:00")
    assert posts.list_posts(conn, now=T0)[0]["posted_at"] == POSTED


def test_post_without_alert_needs_a_title(conn):
    with pytest.raises(posts.PostError):
        posts.create_post(conn, posted_at=POSTED)
    with pytest.raises(posts.PostError):
        posts.create_post(conn, alert_id=1, draft_id=99, posted_at=POSTED)


def test_metrics_validation(conn):
    pid = posts.create_post(conn, alert_id=1, posted_at=POSTED)
    with pytest.raises(posts.PostError):
        posts.add_metrics(conn, pid, observed_at=at(-1), views=10)       # before the post existed
    with pytest.raises(posts.PostError):
        posts.add_metrics(conn, pid, observed_at=at(24))                 # no numbers at all
    with pytest.raises(posts.PostError):
        posts.add_metrics(conn, pid, observed_at=at(24), completion_rate=140)
    with pytest.raises(LookupError):
        posts.add_metrics(conn, 99, observed_at=at(24), views=1)


def test_checkpoints_pick_the_snapshot_closest_to_the_target_age(conn):
    pid = posts.create_post(conn, alert_id=1, posted_at=POSTED)
    a = posts.add_metrics(conn, pid, observed_at=at(20), views=100)
    b = posts.add_metrics(conn, pid, observed_at=at(25), views=150)
    posts.add_metrics(conn, pid, observed_at=at(60), views=300)          # between windows: kept, not compared
    [p] = posts.list_posts(conn, now=T0 + timedelta(hours=61))
    assert p["checkpoints"] == {"24h": b, "7d": None}
    assert a != b and [m["age_hours"] for m in p["metrics"]] == [20, 25, 60]


def test_due_checks(conn):
    pid = posts.create_post(conn, alert_id=1, posted_at=POSTED)
    assert posts.list_posts(conn, now=T0 + timedelta(hours=10))[0]["due"] == []
    assert posts.list_posts(conn, now=T0 + timedelta(hours=20))[0]["due"] == ["24h"]
    posts.add_metrics(conn, pid, observed_at=at(20), views=5)
    assert posts.list_posts(conn, now=T0 + timedelta(hours=21))[0]["due"] == []
    assert posts.list_posts(conn, now=T0 + timedelta(days=6))[0]["due"] == ["7d"]
    assert posts.list_posts(conn, now=T0 + timedelta(days=11))[0]["due"] == []   # window closed


def test_analytics_compares_same_age_and_flags_small_samples(conn):
    for i, (views, shares) in enumerate([(1000, 10), (2000, 60), (100000, 100)]):
        pid = posts.create_post(conn, title=f"roundup {i}", format="weekend", posted_at=POSTED)
        posts.add_metrics(conn, pid, observed_at=at(24), views=views, shares=shares)  # saves not recorded
    pid = posts.create_post(conn, alert_id=1, posted_at=POSTED)
    posts.add_metrics(conn, pid, observed_at=at(168), views=500)                     # only a 7-day number
    out = posts.analytics(conn, "format", now=T0 + timedelta(days=8))
    by = {g["group"]: g for g in out["groups"]}
    wk = by["weekend"]["checkpoints"]["24h"]
    assert (wk["n"], wk["enough"], wk["median_views"]) == (3, True, 2000)           # median ignores the outlier
    assert wk["per_1k_views"]["shares"] == 10.0                                     # 10, 30, 1 per 1k -> 10
    assert wk["per_1k_views"]["saves"] is None                                      # unknown, not zero
    assert by["weekend"]["checkpoints"]["7d"]["n"] == 0
    pre = by["presale"]["checkpoints"]
    assert pre["24h"]["n"] == 0 and (pre["7d"]["n"], pre["7d"]["enough"]) == (1, False)


def test_delete_post_removes_its_metrics(conn):
    pid = posts.create_post(conn, alert_id=1, posted_at=POSTED)
    posts.add_metrics(conn, pid, observed_at=at(24), views=1)
    posts.delete_post(conn, pid)
    assert conn.execute("SELECT COUNT(*) FROM post_metrics").fetchone()[0] == 0


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    detectors.save_alerts(c, [{"key": "k", "kind": "weekend", "title": "Weekend", "score": 50,
                               "facts": {"format": "weekend", "shows": []}}])
    c.close()
    return TestClient(api.app)


def test_post_api_round_trip(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    r = client.post("/api/posts", json={"alert_id": 1, "posted_at": (now - timedelta(hours=20)).isoformat(),
                                        "url": "https://www.tiktok.com/@someone/video/1"})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.get("/api/alerts").json()[0]["posts"][0]["id"] == pid
    s = client.get("/api/summary").json()
    assert (s["posts_this_week"], s["metrics_due"]) == (1, 1)
    assert client.post(f"/api/posts/{pid}/metrics", json={"views": 900, "saves": 12}).status_code == 201
    assert client.get("/api/summary").json()["metrics_due"] == 0
    assert client.patch(f"/api/posts/{pid}", json={"url": None, "notes": "hook was too long"}).status_code == 200
    [p] = client.get("/api/posts").json()
    assert (p["url"], p["notes"], p["metrics"][0]["views"]) == (None, "hook was too long", 900)
    assert client.get("/api/analytics", params={"group_by": "video_style"}).json()["groups"][0]["group"] == "unknown"


def test_post_api_errors(client):
    assert client.post("/api/posts", json={"title": "x", "posted_at": "yesterday"}).status_code == 400
    assert client.post("/api/posts", json={"alert_id": 42, "posted_at": POSTED}).status_code == 400
    assert client.post("/api/posts/42/metrics", json={"views": 1}).status_code == 404
    assert client.patch("/api/posts/42", json={"notes": "x"}).status_code == 404
    assert client.get("/api/analytics", params={"group_by": "hook"}).status_code == 400
