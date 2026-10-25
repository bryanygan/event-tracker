"""The local model isn't started when the PC is short on RAM: templated ideas draft from their template,
the rest wait in the queue, and the batch doesn't crash or retry the start per draft."""
import json
from contextlib import contextmanager

import pytest

from tracker import config, db, drafts, llm

from tests.test_angles import FACTS


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_MIN_FREE_MB", 3500)
    monkeypatch.setattr(llm, "ollama_up", lambda: False)
    monkeypatch.setattr(llm, "free_memory_mb", lambda: 1800)
    monkeypatch.setattr(llm, "_ollama_exe", lambda: pytest.fail("Ollama must not be started"))
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: pytest.fail("no model call when memory is low"))
    return db.connect(tmp_path / "t.db")


def _alert(conn, kind, facts, score=50):
    cur = conn.execute("INSERT INTO alerts (key, kind, title, score, facts, created_at) VALUES (?,?,?,?,?,?)",
                       (f"{kind}:1", kind, kind, score, json.dumps(facts), "2026-10-24T10:00:00+00:00"))
    return cur.lastrowid


def test_low_memory_refuses_to_start_ollama(conn):
    with pytest.raises(llm.LowMemory, match="1.8 GB of memory free"):
        with llm.ollama_session():
            pass


def test_enough_memory_or_unknown_memory_doesnt_block(monkeypatch):
    monkeypatch.setattr(llm, "ollama_up", lambda: False)
    monkeypatch.setattr(llm, "free_memory_mb", lambda: 6000)
    llm.check_memory()
    monkeypatch.setattr(llm, "free_memory_mb", lambda: None)  # couldn't read it: don't block
    llm.check_memory()


def test_model_already_loaded_by_someone_else_is_fine(monkeypatch):
    monkeypatch.setattr(llm, "free_memory_mb", lambda: 500)
    monkeypatch.setattr(llm, "_model_loaded", lambda: True)
    llm.check_memory(up=True)


def test_batch_drafts_templated_ideas_and_leaves_the_rest(conn, monkeypatch):
    starts = []
    real_check = llm.check_memory
    monkeypatch.setattr(llm, "check_memory", lambda up=None: starts.append(1) or real_check(up))
    game = _alert(conn, "big_game", FACTS["big_game"], score=90)
    tour = _alert(conn, "new_tour", FACTS["new_tour"], score=80)
    made = drafts.draft_pending(conn)
    assert len(made) == 1 and len(starts) == 1  # one start attempt for the whole batch
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (made[0],)).fetchone()
    assert d["alert_id"] == game and d["model"] == "template" and "Celtics" in d["hook"]
    assert "#NBA" in d["hashtags"]
    assert conn.execute("SELECT status FROM alerts WHERE id=?", (tour,)).fetchone()["status"] == "new"


def test_claude_fallback_with_low_memory_tries_ollama_once(conn, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "DRAFT_PROVIDER", "claude")
    sessions = []
    real = llm.ollama_session

    @contextmanager
    def session():
        sessions.append(1)
        with real():
            yield
    monkeypatch.setattr(llm, "ollama_session", session)
    monkeypatch.setattr(llm, "_claude_chat", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("overloaded")))
    _alert(conn, "new_tour", FACTS["new_tour"], score=80)
    _alert(conn, "new_tour2", {**FACTS["new_tour"], "artist": "Kilo Moon"}, score=70)
    with llm.model_session():
        for facts in (FACTS["new_tour"], FACTS["new_tour"]):
            with pytest.raises(llm.LowMemory):
                llm.generate(facts, conn=conn)
    assert len(sessions) == 1
