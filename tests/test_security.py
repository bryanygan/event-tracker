"""Local-only API guard, no API key in error messages, bounded inputs."""
from datetime import datetime, timezone

import pytest
import requests
from fastapi.testclient import TestClient

from tracker import api, config, db, posts, trends
from tracker.fetchers import ticketmaster as tm


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    db.connect(path).close()
    return TestClient(api.app)


NOTE = {"kind": "search", "text": "people searching Nova Vale tickets", "terms": ["Nova Vale"],
        "scope": "philly", "confidence": "high"}


def test_reads_work_locally_but_not_through_a_foreign_host_name(client):
    assert client.get("/api/trends").status_code == 200
    assert client.get("/api/trends", headers={"host": "127.0.0.1:8765"}).status_code == 200
    # DNS rebinding: an attacker's domain pointed at 127.0.0.1 still sends its own name as Host
    assert client.get("/api/trends", headers={"host": "evil.example:8765"}).status_code == 403


def test_cross_site_writes_are_refused(client):
    assert client.post("/api/trends", json=NOTE, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/run", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/trends", json=NOTE, headers={"origin": "http://127.0.0.1:8765"}).status_code == 201
    assert client.get("/api/trends", headers={"origin": "https://evil.example"}).status_code == 200  # reads: CORS


def test_oversized_and_chunked_bodies_are_refused(client):
    assert client.post("/api/trends", content=b"{}", headers={"content-length": str(api.MAX_BODY_BYTES + 1),
                                                               "content-type": "application/json"}).status_code == 413
    assert client.post("/api/trends", content=iter([b"{}"]), headers={"content-type": "application/json"}).status_code == 413


class _Resp:
    status_code = 401

    def raise_for_status(self):
        raise requests.HTTPError("401 Client Error: Unauthorized for url: "
                                 "https://app.ticketmaster.com/discovery/v2/attractions.json?keyword=x&apikey=SECRETKEY123")


class _Session:
    def __init__(self, exc=None):
        self.exc = exc

    def get(self, *a, **kw):
        if self.exc:
            raise self.exc
        return _Resp()


@pytest.mark.parametrize("session", [
    _Session(),
    _Session(requests.ConnectionError("HTTPSConnectionPool(host='app.ticketmaster.com'): Max retries exceeded "
                                      "with url: /discovery/v2/events.json?size=200&apikey=SECRETKEY123")),
])
def test_ticketmaster_errors_never_carry_the_key(session):
    client = tm.TicketmasterClient(api_key="SECRETKEY123", session=session, min_interval=0)
    with pytest.raises(requests.RequestException) as e:
        client._get({"keyword": "x"})
    assert "SECRETKEY123" not in str(e.value) and "apikey=***" in str(e.value)


def test_far_out_timestamps_are_a_clean_validation_error():
    with pytest.raises(posts.PostError):
        posts.parse_iso("9999-12-31T23:59:59-14:00")
    assert posts.parse_iso("2026-10-22T10:00:00-04:00") == datetime(2026, 10, 22, 14, tzinfo=timezone.utc)


def test_trend_notes_are_capped(monkeypatch):
    monkeypatch.setattr(config, "TREND_NOTES_MAX", 2)
    c = db.connect(":memory:")
    for _ in range(2):
        trends.create_note(c, **NOTE)
    with pytest.raises(trends.TrendError):
        trends.create_note(c, **NOTE)
