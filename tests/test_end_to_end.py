"""Whole pipeline from a cached pull, with the LLM and Deezer stubbed out."""
import json
from contextlib import nullcontext

import pytest
import responses

from tracker import db, llm, run as pipeline
from tracker.fetchers import deezer
from tests.factory import tm_event


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "tm_last_pull.json"
    monkeypatch.setattr(pipeline, "RAW_CACHE", cache)
    monkeypatch.setattr(llm, "ollama_session", nullcontext)
    monkeypatch.setattr(deezer, "enrich_headliners", lambda conn: 0)

    def fake_generate(facts, model=None, script=None, caption=None, conn=None):
        return {"hook": "Philly, listen up", "script": script or f"{facts['format']} script", "caption": caption or "Save this",
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
def test_deezer_enrichment_picks_exact_match_with_most_fans(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    from tracker import store
    from tracker.fetchers import ticketmaster as tm
    r = store.start_run(conn, "x")
    store.upsert_events(conn, r, [tm.normalize(tm_event(name="Mt. Joy"))], baseline=True)

    responses.get(deezer.SEARCH_URL, json={"data": [
        {"id": 1, "name": "Mt. Joy Tribute", "nb_fan": 9_000_000},
        {"id": 2, "name": "Mt. Joy", "nb_fan": 120},
        {"id": 3, "name": "mt. joy", "nb_fan": 250_000},
    ]})
    responses.get(deezer.ALBUMS_URL.format(3), json={"data": []})
    responses.get(deezer.GENRES_URL, json={"data": []})
    assert deezer.enrich_headliners(conn, deezer.DeezerClient(min_interval=0)) == 1
    row = conn.execute("SELECT * FROM attractions WHERE name='Mt. Joy'").fetchone()
    assert (row["fans"], row["popularity"]) == (250_000, 68)
    assert deezer.enrich_headliners(conn, deezer.DeezerClient(min_interval=0)) == 0  # cached


def test_popularity_scale():
    assert [deezer.popularity_from_fans(f) for f in (None, 50, 10_000, 1_000_000, 10**9)] == [None, 0, 40, 80, 100]


def test_migration_adds_columns_to_old_db(tmp_path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "old.db")
    old.execute("CREATE TABLE attractions (id TEXT PRIMARY KEY, name TEXT NOT NULL, genre TEXT, subgenre TEXT, "
                "tm_upcoming_total INTEGER, spotify_popularity INTEGER, first_seen TEXT NOT NULL)")
    old.commit(); old.close()
    cols = {r["name"] for r in db.connect(tmp_path / "old.db").execute("PRAGMA table_info(attractions)")}
    assert {"fans", "popularity", "popularity_checked_at"} <= cols


@responses.activate
def test_deezer_non_quota_error_skips_artist_but_quota_error_stops():
    client = deezer.DeezerClient(min_interval=0)
    responses.get(deezer.SEARCH_URL, json={"error": {"type": "ParameterException", "code": 500}})
    assert client.find_artist("x") is None
    responses.replace(responses.GET, deezer.SEARCH_URL, json={"error": {"type": "Exception", "code": 4}})
    with pytest.raises(Exception, match="quota"):
        client.find_artist("y")


def test_draft_retries_once_when_flagged(tmp_path, monkeypatch):
    from tracker import detectors, drafts
    conn = db.connect(tmp_path / "r.db")
    detectors.save_alerts(conn, [{"key": "k", "kind": "weekend", "title": "t", "score": 1,
                                  "facts": {"format": "weekend", "weekend": "Fri, Oct 23", "shows": []}}])
    outputs = iter([{"hook": "Oct 31 at 9pm!", "script": "", "caption": "", "hashtags": [], "model": "m"},
                    {"hook": "Fri, Oct 23 is it", "script": "", "caption": "", "hashtags": [], "model": "m"}])
    monkeypatch.setattr(llm, "generate", lambda facts, model=None, script=None, caption=None, conn=None: next(outputs))
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, 1),)).fetchone()
    assert d["hook"] == "Fri, Oct 23 is it" and json.loads(d["warnings"]) == []


def test_midday_run_detects_but_never_drafts_or_looks_up(env, monkeypatch):
    cache, conn = env
    cache.write_text(json.dumps([tm_event(event_id="a", name="Old Band")]))
    pipeline.run(from_cache=True, conn=conn, midday=True)
    cache.write_text(json.dumps([tm_event(event_id="a", name="Old Band"),
                                 tm_event(event_id="b", name="Huge Star", venue="Lincoln Financial Field", upcoming=90,
                                          date="2027-06-01")]))

    def boom(*a, **kw):
        raise AssertionError("midday must not start Ollama, look things up or publish")
    monkeypatch.setattr(llm, "generate", boom)
    monkeypatch.setattr(llm, "ollama_session", boom)
    monkeypatch.setattr(pipeline.context, "auto_lookup", boom)
    monkeypatch.setattr(pipeline.publish, "publish", boom)
    out = pipeline.run(from_cache=True, conn=conn, midday=True)
    assert out["new_alerts"] >= 1 and out["drafts"] == 0
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
    assert any(r["kind"] == "new_tour" for r in conn.execute("SELECT kind FROM alerts"))


def test_only_one_run_at_a_time():
    with pipeline.single_run():
        with pytest.raises(pipeline.RunBusy):
            with pipeline.single_run():
                pass
    with pipeline.single_run():  # released afterwards
        pass


def test_busy_scheduled_run_exits_quietly(monkeypatch, caplog):
    monkeypatch.setattr(pipeline, "setup_logging", lambda: None)
    with pipeline.single_run(), caplog.at_level("INFO"):
        pipeline.main(["--midday"])
    assert "Skipped this midday run" in caplog.text
