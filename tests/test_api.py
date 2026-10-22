import json

import pytest
from fastapi.testclient import TestClient

from tracker import api, db, detectors, store
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    r = store.start_run(c, "ticketmaster")
    store.upsert_events(c, r, [tm.normalize(tm_event(event_id="e1", name="Mt. Joy", date="2099-01-01"))],
                        baseline=True)
    store.finish_run(c, r, fetched=1, api_calls=0, ok=True)
    detectors.save_alerts(c, [{"key": "k", "kind": "weekend", "title": "Weekend", "score": 50,
                               "facts": {"format": "weekend", "shows": []}}])
    c.execute("""INSERT INTO drafts (alert_id, format, hook, script, caption, hashtags, onscreen, warnings, created_at)
                 VALUES (1, 'weekend', 'h', 's', 'c', '#philly', '["line"]', '[]', 'now')""")
    c.commit()
    return TestClient(api.app)


def test_summary_and_events(client):
    s = client.get("/api/summary").json()
    assert s["upcoming_events"] == 1 and s["open_alerts"] == 1 and s["pending_drafts"] == 1
    events = client.get("/api/events", params={"q": "joy", "days": 40000}).json()
    assert events[0]["headliner"] == "Mt. Joy"


def test_alerts_include_drafts_and_can_be_dismissed(client):
    [a] = client.get("/api/alerts").json()
    assert a["drafts"][0]["onscreen"] == ["line"]
    assert client.patch("/api/alerts/1", json={"status": "dismissed"}).status_code == 200
    assert client.get("/api/alerts").json() == []
    assert len(client.get("/api/alerts", params={"status": "dismissed"}).json()) == 1


def test_draft_edit_and_status(client):
    assert client.patch("/api/drafts/1", json={"status": "approved", "hook": "better hook"}).status_code == 200
    d = client.get("/api/alerts").json()[0]["drafts"][0]
    assert (d["status"], d["hook"]) == ("approved", "better hook")
    assert client.patch("/api/drafts/1", json={"status": "posted-it"}).status_code == 400
    assert client.patch("/api/drafts/99", json={"status": "approved"}).status_code == 404


def test_static_files_served_without_path_escape(client, tmp_path, monkeypatch):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<html>app</html>")
    (tmp_path / "dist" / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setattr(api, "DIST", tmp_path / "dist")
    assert client.get("/favicon.svg").text == "<svg/>"
    assert client.get("/alerts").text == "<html>app</html>"       # SPA fallback
    assert "nope" not in client.get("/..%2Fsecret.txt").text


def test_connection_can_be_closed_from_another_thread(tmp_path):
    """Regression: parallel page-load requests 500'd when FastAPI tore down get_db on another thread."""
    import threading
    from tracker import db
    c = db.connect(tmp_path / "t.db")
    errors = []
    t = threading.Thread(target=lambda: errors.append(None) if c.close() is None else None)
    t.start(); t.join()
    assert errors == [None]
