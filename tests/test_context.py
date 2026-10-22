"""Web context: cached per artist, capped per day, never spent in tests, kept out of drafts."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, config, context, db, detectors

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)  # Thu 10 AM ET
SHOW = {"artist": "Nova Vale", "date": "Sat, Nov 7", "time": "8:00 PM", "venue": "Union Transfer", "url": "u"}
PRESALE = {"format": "presale", "artist": "Nova Vale", "sale_name": "Artist Presale",
           "sale_opens": "Thu, Oct 22 at 10:00 AM ET", "shows": [SHOW]}
WEEKEND = {"format": "weekend", "weekend": "Fri, Oct 23 – Sun, Oct 25", "shows": [SHOW]}

# Shape of a real /v1/agent response on the fast preset (output_text is null there).
BODY = {"output_text": None, "usage": {"cost": {"total_cost": 0.0016}}, "output": [
    {"type": "search_results", "results": [
        {"id": 1, "title": "Nova Vale announces tour", "url": "https://news.example/tour", "date": "2026-10-13"},
        {"id": 2, "title": "Unrelated page", "url": "https://x.example/other", "date": None},
        {"id": 3, "title": "Nova Vale new album", "url": "https://news.example/album", "date": "2026-10-16"}]},
    {"type": "message", "content": [{"type": "output_text",
                                     "text": "- **Oct. 13:** Tour announced.[1]\n- **Oct. 16:** New album out.[3]"}]}]}


class Resp:
    def __init__(self, status=200, body=BODY):
        self.status_code, self._body, self.content = status, body, b"x"

    def json(self):
        return self._body


class Session:
    def __init__(self, resp=None):
        self.calls, self.resp = [], resp or Resp()

    def post(self, url, **kw):
        self.calls.append(kw["json"])
        return self.resp


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "test-key")
    c = db.connect(":memory:")
    detectors.save_alerts(c, [{"key": "p", "kind": "presale_soon", "title": "t", "score": 50, "facts": PRESALE},
                              {"key": "w", "kind": "weekend", "title": "t", "score": 50, "facts": WEEKEND}])
    return c


def test_tests_never_have_the_real_key():
    assert config.PERPLEXITY_API_KEY == ""


def test_prompt_states_the_date_and_only_public_show_facts():
    p = context.build_prompt({**PRESALE, "your_plays": 99}, NOW)
    assert "Today is Thursday, October 22, 2026" in p and "August 23, 2026" in p
    assert "Sat, Nov 7 at Union Transfer" in p and "99" not in p


def test_parse_keeps_only_cited_sources():
    text, sources = context.parse(BODY)
    assert text.startswith("- **Oct. 13:**")
    assert [s["id"] for s in sources] == [1, 3] and sources[0]["date"] == "2026-10-13"


def test_lookup_is_cached_per_artist(conn):
    s = Session()
    first = context.lookup(conn, 1, now=NOW, session=s)
    again = context.lookup(conn, 1, now=NOW + timedelta(days=6), session=s)
    assert len(s.calls) == 1 and again["answer"] == first["answer"] and again["fresh"]
    assert s.calls[0]["preset"] == "fast" and s.calls[0]["tools"] == [{"type": "web_search"}]
    stale = context.cached(conn, "nova vale", NOW + timedelta(days=config.CONTEXT_CACHE_DAYS + 1))
    assert stale and not stale["fresh"]
    context.lookup(conn, 1, now=NOW + timedelta(days=8), session=s)
    assert len(s.calls) == 2
    assert context.usage(conn, NOW)["cost_today_usd"] == 0.0016


def test_daily_cap_counts_failures(conn, monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_DAILY_CAP", 2)
    bad = Session(Resp(500, {"error": {"message": "boom"}}))
    for _ in range(2):
        with pytest.raises(context.ContextError) as e:
            context.lookup(conn, 1, now=NOW, session=bad)
        assert e.value.status == 502
    with pytest.raises(context.ContextError) as e:
        context.lookup(conn, 1, now=NOW, session=Session())
    assert e.value.status == 429 and len(bad.calls) == 2
    assert context.cached(conn, "nova vale", NOW) is None  # failures are never served as context
    # the cap resets at midnight ET, not UTC
    assert context.usage(conn, NOW + timedelta(hours=11))["today"] == 2  # Oct 23 in UTC, still Oct 22 ET
    assert context.usage(conn, NOW + timedelta(hours=15))["today"] == 0  # 1 AM ET Oct 23


def test_roundups_and_missing_key_are_refused(conn, monkeypatch):
    with pytest.raises(context.ContextError) as e:
        context.lookup(conn, 2, now=NOW, session=Session())
    assert e.value.status == 400
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "")
    with pytest.raises(context.ContextError) as e:
        context.lookup(conn, 1, now=NOW, session=Session())
    assert e.value.status == 503


def test_auto_lookup_only_strong_ideas_within_budget(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(context, "lookup", lambda c, alert_id, **kw: calls.append((alert_id, kw["trigger"])))
    queue = [{"alert_id": 1, "band": "strong", "stage": "new"}, {"alert_id": 2, "band": "strong", "stage": "new"}]
    monkeypatch.setattr(context.opportunities, "queue", lambda c, now: queue)
    assert context.auto_lookup(conn, NOW) == 1 and calls == [(1, "auto")]  # the weekend has no subject
    queue[0]["band"] = "medium"
    calls.clear()
    assert context.auto_lookup(conn, NOW) == 0 and calls == []
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "")
    queue[0]["band"] = "strong"
    assert context.auto_lookup(conn, NOW) == 0


def test_api_shows_cached_context_and_maps_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "test-key")
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    detectors.save_alerts(c, [{"key": "p", "kind": "presale_soon", "title": "t", "score": 50, "facts": PRESALE},
                              {"key": "w", "kind": "weekend", "title": "t", "score": 50, "facts": WEEKEND}])
    context.lookup(c, 1, session=Session())
    c.close()
    client = TestClient(api.app)
    alerts = {a["id"]: a for a in client.get("/api/alerts").json()}
    assert alerts[1]["context"]["sources"][0]["url"] == "https://news.example/tour" and alerts[1]["context_supported"]
    assert alerts[2]["context"] is None and not alerts[2]["context_supported"]
    r = client.post("/api/alerts/1/context")  # fresh cache: no call needed (the real session would fail here)
    assert r.status_code == 200 and r.json()["usage"]["today"] == 1
    assert client.post("/api/alerts/2/context").status_code == 400
    assert client.post("/api/alerts/99/context").status_code == 404
