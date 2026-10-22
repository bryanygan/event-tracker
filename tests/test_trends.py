"""Trend notes: validated input, exact matching, expiry, the national label, and the queue's search component."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, config, db, detectors, opportunities, trends
from tests.test_opportunities import NOW, alert, ev, show

NOTE = {"kind": "search", "text": "nova vale tickets philly", "terms": ["Nova Vale"], "scope": "philly",
        "confidence": "high"}


def note(**kw):
    """A stored-note-shaped dict (what list_notes returns)."""
    base = {"id": 1, **NOTE, "observed_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=7)).isoformat(),
            "expired": False}
    return {**base, **kw}


def search(result):
    return next(c for c in result["components"] if c["key"] == "search")


def test_no_notes_keeps_search_unknown_exactly_as_before():
    out = ev(alert())
    assert search(out)["value"] is None and out["coverage"] == 0.9 and out["trend_notes"] == []
    assert not any("Search" in c for c in out["caveats"])


def test_exact_artist_match_fills_search_and_counts_as_a_reason():
    out = opportunities.evaluate(alert(), [show()], None, [], [], NOW, [note()])
    s = search(out)
    assert s["value"] == 100 and out["coverage"] == 1.0
    assert s["why"].startswith("Philly search trend, high confidence") and "Nova Vale" in s["why"]
    assert s["why"] in out["reasons"]
    assert out["trend_notes"][0]["match"] == "exact"


def test_national_notes_are_labelled_and_discounted():
    out = opportunities.evaluate(alert(), [show()], None, [], [], NOW, [note(scope="national")])
    assert search(out)["value"] == 70
    assert search(out)["why"].startswith("National (not Philly-specific) search trend")
    unknown = opportunities.evaluate(alert(), [show()], None, [], [], NOW, [note(scope="unknown", confidence="low")])
    assert search(unknown)["value"] == 30 and search(unknown)["why"].startswith("Scope unknown")


def test_expired_notes_dont_count_but_are_shown():
    out = opportunities.evaluate(alert(), [show()], None, [], [], NOW, [note(expired=True)])
    assert search(out)["value"] is None
    assert out["trend_notes"][0]["expired"] is True


def test_matching_is_exact_or_whole_word_never_fuzzy():
    terms = trends.idea_terms(alert()["facts"], [show()])
    assert {"nova vale", "union transfer", "rock"} <= terms
    assert trends.match(note(terms=["Nova"]), terms, "New show: Nova Vale") == ("Nova", "title")  # whole word in title
    assert trends.match(note(terms=["Nov"]), terms, "New show: Nova Vale") is None               # not a substring
    assert trends.match(note(terms=["nova  VALE"]), terms, "") == ("nova  VALE", "exact")         # case/space-insensitive
    assert trends.match(note(terms=["Union Transfer"]), terms, "") == ("Union Transfer", "exact")  # venue
    # a title hit counts for less than an exact match
    out = opportunities.evaluate(alert(title="New show: Nova Vale"), [show()], None, [], [], NOW, [note(terms=["Nova"])])
    assert search(out)["value"] == 80


def test_best_note_wins_and_others_are_counted():
    notes = [note(id=1, confidence="low"), note(id=2, confidence="high", text="nova vale sound", kind="sound")]
    out = opportunities.evaluate(alert(), [show()], None, [], [], NOW, notes)
    assert search(out)["value"] == 100 and "(+1 more)" in search(out)["why"] and "sound" in search(out)["why"]


@pytest.mark.parametrize("bad, msg", [
    ({"kind": "vibe"}, "kind must be"),
    ({"scope": "global"}, "scope must be"),
    ({"confidence": "certain"}, "confidence must be"),
    ({"text": "  "}, "can't be empty"),
    ({"text": "x" * 201}, "too long"),
    ({"source_url": "javascript:alert(1)"}, "http(s)"),
    ({"source_url": "ftp://x.example/a"}, "http(s)"),
    ({"terms": []}, "at least one"),
    ({"terms": ["x" * 81]}, "at most"),
    ({"observed_at": "2026-10-21T19:00"}, "timezone"),
    ({"observed_at": "yesterday"}, "not a timestamp"),
    ({"momentum": "viral"}, "momentum must be"),
])
def test_validation(bad, msg):
    with pytest.raises(trends.TrendError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        trends.validate({**NOTE, **bad})


def test_create_defaults_and_expiry(monkeypatch):
    c = db.connect(":memory:")
    nid = trends.create_note(c, now=NOW, **{**NOTE, "observed_at": "2026-10-21T08:00:00-04:00", "terms": "Nova Vale, R&B"})
    [n] = trends.list_notes(c, now=NOW)
    assert n["id"] == nid and n["observed_at"] == "2026-10-21T12:00:00+00:00"
    assert n["expires_at"] == "2026-10-28T12:00:00+00:00" and n["terms"] == ["Nova Vale", "R&B"]
    assert trends.list_notes(c, now=NOW + timedelta(days=8))[0]["expired"]
    assert trends.list_notes(c, now=NOW + timedelta(days=8), include_expired=False) == []
    with pytest.raises(trends.TrendError, match="after observed_at"):
        trends.create_note(c, now=NOW, **{**NOTE, "observed_at": NOW.isoformat(), "expires_at": (NOW - timedelta(hours=1)).isoformat()})
    trends.update_note(c, nid, confidence="low", expires_at=None)  # clearing expiry resets the default
    assert trends.list_notes(c, now=NOW)[0]["confidence"] == "low"
    trends.delete_note(c, nid)
    with pytest.raises(LookupError):
        trends.delete_note(c, nid)


def test_api_crud_and_queue_effect(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    show_ = {"artist": "Nova Vale", "date": "Sat, Nov 7", "time": "8:00 PM", "venue": "Union Transfer", "url": "u"}
    detectors.save_alerts(c, [{"key": "k", "kind": "new_tour", "title": "New Philly show: Nova Vale", "score": 60,
                               "facts": {"format": "new_tour", "artist": "Nova Vale", "shows": [show_]}}])
    c.close()
    client = TestClient(api.app)
    before = client.get("/api/queue").json()["items"][0]
    assert next(x for x in before["components"] if x["key"] == "search")["value"] is None

    assert client.post("/api/trends", json={**NOTE, "source_url": "javascript:x"}).status_code == 400
    assert client.post("/api/trends", json={**NOTE, "kind": None}).status_code == 400  # missing kind
    r = client.post("/api/trends", json={**NOTE, "scope": "national", "source_url": "https://www.tiktok.com/tag/x"})
    assert r.status_code == 201
    nid = r.json()["id"]
    item = client.get("/api/queue").json()["items"][0]
    assert item["rules_version"] == config.OPPORTUNITY_RULES_VERSION
    assert item["trend_notes"][0]["id"] == nid and item["trend_notes"][0]["scope"] == "national"
    assert next(x for x in item["components"] if x["key"] == "search")["why"].startswith("National (not Philly-specific)")

    assert client.patch(f"/api/trends/{nid}", json={"scope": "philly"}).status_code == 200
    assert client.patch(f"/api/trends/{nid}", json={"text": None}).status_code == 400
    assert client.patch("/api/trends/999", json={"scope": "philly"}).status_code == 404
    assert client.get("/api/trends").json()[0]["scope"] == "philly"
    past = datetime.now(timezone.utc) - timedelta(days=30)
    assert client.patch(f"/api/trends/{nid}", json={"observed_at": past.isoformat(),
                                                     "expires_at": (past + timedelta(days=1)).isoformat()}).status_code == 200
    item = client.get("/api/queue").json()["items"][0]
    assert item["trend_notes"][0]["expired"] and next(x for x in item["components"] if x["key"] == "search")["value"] is None
    assert client.delete(f"/api/trends/{nid}").status_code == 200
    assert client.delete(f"/api/trends/{nid}").status_code == 404
    assert client.get("/api/trends").json() == []
