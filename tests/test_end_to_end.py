"""Whole pipeline from a cached pull, with the LLM and Spotify stubbed out."""
import json
from contextlib import nullcontext

import pytest
import responses

from tracker import db, llm, run as pipeline
from tracker.fetchers import spotify
from tests.factory import tm_event


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "tm_last_pull.json"
    monkeypatch.setattr(pipeline, "RAW_CACHE", cache)
    monkeypatch.setattr(llm, "ollama_session", nullcontext)
    monkeypatch.setattr(spotify.SpotifyClient, "configured", property(lambda self: False))

    def fake_generate(facts, model=None):
        return {"hook": "Philly, listen up", "script": f"{facts['format']} script", "caption": "Save this",
                "hashtags": ["#philly"], "model": "fake"}
    monkeypatch.setattr(llm, "generate", fake_generate)
    return cache, db.connect(tmp_path / "t.db")


def test_two_runs_produce_alerts_and_drafts(env):
    cache, conn = env
    cache.write_text(json.dumps([tm_event(event_id="a", name="Old Band")]))
    first = pipeline.run(from_cache=True, conn=conn)
    assert first["changes"] == {}  # baseline

    cache.write_text(json.dumps([
        tm_event(event_id="a", name="Old Band"),
        tm_event(event_id="b", name="Huge Star", venue="Lincoln Financial Field", upcoming=90, date="2027-06-01"),
        tm_event(event_id="p", name="Parking Lot C - Huge Star"),
    ]))
    second = pipeline.run(from_cache=True, conn=conn)
    assert second["changes"] == {"new": 1}  # parking pass filtered out
    kinds = {r["kind"] for r in conn.execute("SELECT kind FROM alerts")}
    assert "new_tour" in kinds
    d = conn.execute("SELECT d.*, a.kind FROM drafts d JOIN alerts a ON a.id=d.alert_id WHERE a.kind='new_tour'").fetchone()
    assert d["script"] == "new_tour script"
    assert json.loads(d["onscreen"])[0] == "JUST ANNOUNCED: Huge Star"

    third = pipeline.run(from_cache=True, conn=conn)
    assert third["new_alerts"] == 0 and third["drafts"] == 0  # nothing re-alerted


@responses.activate
def test_spotify_enrichment_picks_exact_match(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    from tracker import store
    from tracker.fetchers import ticketmaster as tm
    r = store.start_run(conn, "x")
    store.upsert_events(conn, r, [tm.normalize(tm_event(name="Mt. Joy"))], baseline=True)

    responses.post("https://accounts.spotify.com/api/token", json={"access_token": "t", "expires_in": 3600})
    responses.get("https://api.spotify.com/v1/search", json={"artists": {"items": [
        {"id": "wrong", "name": "Mt. Joy Tribute", "popularity": 90},
        {"id": "right", "name": "Mt. Joy", "popularity": 66, "followers": {"total": 900000}, "genres": ["indie folk"]},
    ]}})
    n = spotify.enrich_headliners(conn, spotify.SpotifyClient("id", "secret"))
    assert n == 1
    row = conn.execute("SELECT * FROM attractions WHERE name='Mt. Joy'").fetchone()
    assert (row["spotify_id"], row["spotify_popularity"], row["spotify_genres"]) == ("right", 66, "indie folk")
    assert spotify.enrich_headliners(conn, spotify.SpotifyClient("id", "secret")) == 0  # cached for 30 days
