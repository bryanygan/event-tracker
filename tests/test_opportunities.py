"""The content queue's opportunity score: separate from notability, explainable, honest about unknowns."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, config, db, detectors, opportunities, posts
from tests.factory import tm_event
from tests.test_pipeline_data import ingest

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)  # Wed noon ET


def alert(kind="new_tour", score=60, created=NOW, post_by=None, expires=None, facts=None, **kw):
    return {"id": 1, "kind": kind, "score": score, "created_at": created.isoformat(), "status": "new",
            "post_by": post_by and post_by.isoformat(), "expires_at": expires and expires.isoformat(),
            "event_ids": None, "shortlisted": 0, "snoozed_until": None,
            "facts": facts or {"artist": "Nova Vale", "shows": [{"artist": "Nova Vale", "date": "Sat, Nov 7",
                                                                 "venue": "Union Transfer", "url": "u", "time": "8 PM"}]},
            **kw}


def show(**kw):
    return {"id": "e1", "headliner": "Nova Vale", "popularity": 50, "venue_tier": 50, "venue_name": "Union Transfer",
            "my_plays": 0, "niche": False, "genre": "Rock", **kw}


def ev(a, events=None, draft=None, posts_=(), recent=(), now=NOW):
    return opportunities.evaluate(a, [show()] if events is None else events, draft, list(posts_), list(recent), now)


def comp(result, key):
    return next(c for c in result["components"] if c["key"] == key)


def test_unknown_search_is_left_out_not_counted_as_zero():
    r = ev(alert())
    assert comp(r, "search")["value"] is None and r["coverage"] == 0.9
    known = [c for c in r["components"] if c["value"] is not None]
    assert r["score"] == round(sum(c["weight"] * c["value"] for c in known) / 0.9, 1)


def test_news_goes_stale():
    fresh = ev(alert(created=NOW - timedelta(hours=2)))
    old = ev(alert(created=NOW - timedelta(days=5)))
    assert comp(fresh, "timing")["value"] == 100 and comp(old, "timing")["value"] == 20
    gone = ev(alert(created=NOW - timedelta(days=config.NEWS_EXPIRES_DAYS + 1)))
    assert gone["stage"] == "expired"


def test_deadline_timing():
    soon = ev(alert("presale_soon", post_by=NOW + timedelta(hours=20), expires=NOW + timedelta(days=2)))
    assert comp(soon, "timing")["value"] == 100 and "20h left" in comp(soon, "timing")["why"]
    late = ev(alert("presale_soon", post_by=NOW - timedelta(hours=1), expires=NOW + timedelta(days=1)))
    assert comp(late, "timing")["value"] == 30
    unknown = ev(alert("presale_soon"))  # an old alert without timing: unknown, not "bad"
    assert comp(unknown, "timing")["value"] is None and unknown["coverage"] == 0.65
    assert any("No deadline recorded" in c for c in unknown["caveats"])


def test_stale_big_show_doesnt_beat_a_timely_niche_story():
    """Roadmap acceptance: high notability alone shouldn't top the queue."""
    big_old = ev(alert(score=92, created=NOW - timedelta(days=5),
                       facts={"artist": "Arena Act", "already_had_philly_dates": True, "shows": []}),
                 events=[show(headliner="Arena Act", popularity=95, venue_tier=90)])
    niche_new = ev(alert(score=48, created=NOW - timedelta(hours=3)),
                   events=[show(popularity=40, venue_tier=15, niche=True, genre="Hip-Hop/Rap")])
    assert big_old["score"] < niche_new["score"]


def test_story_rules():
    comeback = {"artist": "Nova Vale", "shows": [], "history": {"is_comeback": True, "years_since_last_philly_show": 8,
                                                                  "last_philly_show_was_multi_act": False}}
    assert comp(ev(alert("presale_soon", facts=comeback)), "story")["value"] == 90
    assert "crowd-sourced" in " ".join(ev(alert("presale_soon", facts=comeback))["caveats"])
    small_room = ev(alert("presale_soon"), events=[show(popularity=80, venue_tier=20, venue_name="MilkBoy")])
    assert comp(small_room, "story")["why"] == "Big name (popularity 80) in a small room (MilkBoy)"


def test_your_lane():
    assert comp(ev(alert(), events=[show(my_plays=40)]), "local")["value"] == 100
    assert comp(ev(alert(), events=[show(niche=True, genre="R&B")]), "local")["why"] == "Philly show in your lane (R&B)"
    assert comp(ev(alert()), "local")["value"] == 70


def test_stages():
    draft = {"id": 1, "status": "pending", "hook": "h", "warnings": [], "facts_changes": []}
    assert ev(alert())["stage"] == "new"
    assert ev(alert(shortlisted=1))["stage"] == "shortlisted"
    assert ev(alert(), draft=draft)["stage"] == "review"
    assert ev(alert(), draft={**draft, "facts_changes": ["Added: X"]})["stage"] == "needs_refresh"
    assert ev(alert(), draft={**draft, "status": "approved"})["stage"] == "approved"
    assert ev(alert(snoozed_until=(NOW + timedelta(hours=1)).isoformat()))["stage"] == "snoozed"
    assert ev(alert(), posts_=[{"has_metrics": 0}])["stage"] == "posted"
    assert ev(alert(status="dismissed"))["stage"] == "dismissed"
    assert ev(alert(), events=[])["stage"] == "expired"  # its shows aren't listed anymore


def test_recently_posted_warning():
    recent = [{"alert_id": 9, "posted_at": (NOW - timedelta(days=2)).isoformat(), "artists": {"nova vale"}}]
    assert any("You posted about nova vale" in c for c in ev(alert(), recent=recent)["caveats"])
    assert not any("You posted" in c for c in ev(alert(), recent=[{**recent[0], "alert_id": 1}])["caveats"])


def test_detectors_record_timing_from_data(monkeypatch):
    conn = db.connect(":memory:")
    raws = [tm_event(event_id=f"r{i}", name=f"Band {i}", date="2026-10-24", genre=g) for i, g in enumerate(["Rock", "Pop", "R&B"])]
    raws.append(tm_event(event_id="p", name="Presale Act", date="2026-12-01", venue="Wells Fargo Center", upcoming=80,
                         onsale="2026-10-23T14:00:00Z", presales=[("Fan presale", "2026-10-22T14:00:00Z", "2026-10-23T03:00:00Z")]))
    ingest(conn, raws)
    [wk] = detectors.detect_weekend(conn, NOW.date())
    assert wk["post_by"] == "2026-10-23T21:00:00+00:00" and wk["expires_at"] == "2026-10-24T22:00:00+00:00"  # Fri 5 PM / Sat 6 PM ET
    assert sorted(wk["event_ids"]) == ["r0", "r1", "r2"]
    [sale] = detectors.detect_sales_soon(conn, NOW, NOW.date().isoformat())
    assert (sale["post_by"], sale["expires_at"]) == ("2026-10-22T14:00:00+00:00", "2026-10-23T14:00:00+00:00")
    detectors.save_alerts(conn, [wk, sale])
    q = opportunities.queue(conn, now=NOW)
    assert {i["stage"] for i in q} == {"new"} and all(i["post_by"] for i in q)


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    ingest(c, [tm_event(event_id="x", name="Nova Vale", date="2099-01-10")])
    detectors.save_alerts(c, [{"key": "k", "kind": "new_tour", "title": "New Philly show: Nova Vale", "score": 60,
                               "facts": {"artist": "Nova Vale", "shows": [{"artist": "Nova Vale", "date": "Sat, Jan 10"}]},
                               "event_ids": ["x"]}])
    c.close()
    return TestClient(api.app)


def test_queue_api_actions_and_post_snapshot(client):
    q = client.get("/api/queue").json()
    [item] = q["items"]
    assert item["stage"] == "new" and item["band"] and q["counts"]["new"] == 1
    assert client.patch("/api/alerts/1", json={"shortlisted": True, "note": "film at the venue"}).status_code == 200
    [item] = client.get("/api/queue").json()["items"]
    assert item["shortlisted"] and item["stage"] == "shortlisted" and item["note"] == "film at the venue"
    later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    client.patch("/api/alerts/1", json={"snoozed_until": later})
    assert client.get("/api/queue").json()["items"][0]["stage"] == "snoozed"
    client.patch("/api/alerts/1", json={"snoozed_until": None})
    assert client.patch("/api/alerts/1", json={"status": "bogus"}).status_code == 400
    assert client.patch("/api/alerts/1", json={"snoozed_until": "tomorrow"}).status_code == 400
    assert client.patch("/api/alerts/9", json={"note": "x"}).status_code == 404

    client.post("/api/posts", json={"alert_id": 1, "posted_at": datetime.now(timezone.utc).isoformat()})
    [p] = client.get("/api/posts").json()
    assert p["opportunity_snapshot"]["stage"] == "shortlisted"           # what the queue said when you chose it
    assert p["opportunity_snapshot"]["rules_version"] == config.OPPORTUNITY_RULES_VERSION
    assert client.get("/api/queue").json()["items"][0]["stage"] == "posted"
