"""Claude drafting: same prompt and checks as Ollama, a daily cap, a cost log, and Ollama as the fallback."""
import json
from contextlib import nullcontext
from types import SimpleNamespace

import anthropic
import pytest

from tracker import config, db, llm

from tests.test_angles import FACTS

DRAFT = {"hook": "Nova Vale just announced Philly", "script": "Nova Vale is coming to Union Transfer.",
         "caption": "Who's going? #philly", "hashtags": ["#novavale"]}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "DRAFT_PROVIDER", "claude")
    monkeypatch.setattr(config, "CLAUDE_MODEL", "claude-haiku-5-5")
    monkeypatch.setattr(llm, "ollama_session", nullcontext)
    return db.connect(tmp_path / "t.db")


def fake_claude(monkeypatch, reply=None, error=None, sent=None):
    class Messages:
        def create(self, **kw):
            if sent is not None:
                sent.append(kw)
            if error:
                raise error
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=3000, output_tokens=400),
                                   content=[SimpleNamespace(type="thinking", thinking=""),
                                            SimpleNamespace(type="text", text=json.dumps(reply or DRAFT))])

    class Client:
        def __init__(self, **kw):
            self.messages = Messages()
    monkeypatch.setattr(anthropic, "Anthropic", Client)


def fake_ollama(monkeypatch, calls):
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": json.dumps({**DRAFT, "hook": "from ollama"})}}
    monkeypatch.setattr(llm.requests, "post", lambda url, timeout, json: calls.append(json["model"]) or Resp())


def test_claude_writes_the_draft_and_logs_its_cost(conn, monkeypatch):
    sent, ollama = [], []
    fake_claude(monkeypatch, sent=sent)
    fake_ollama(monkeypatch, ollama)
    out = llm.generate(FACTS["new_tour"], conn=conn)
    assert out["model"] == "claude-haiku-5-5" and out["hook"] == "Nova Vale just announced Philly"
    assert out["hashtags"][0] == "#philly" and out["caption"] == "Who's going?"  # same cleanup as Ollama
    assert not ollama
    req = sent[0]
    assert req["system"] == llm.SYSTEM_PROMPT and req["output_config"]["effort"] == "low"
    assert req["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "FACTS (the only things you may state)" in req["messages"][0]["content"]
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert (row["input_tokens"], row["output_tokens"], row["error"]) == (3000, 400, None)
    assert row["cost_usd"] == pytest.approx((3000 * 0.10 + 400 * 0.50) / 1e6)
    usage = llm.claude_usage(conn)
    assert usage["today"] == 1 and usage["month_calls"] == 1 and usage["enabled"]


def test_api_failure_falls_back_to_ollama_and_counts(conn, monkeypatch):
    ollama = []
    fake_claude(monkeypatch, error=RuntimeError("overloaded"))
    fake_ollama(monkeypatch, ollama)
    out = llm.generate(FACTS["new_tour"], conn=conn)
    assert out["model"] == config.OLLAMA_MODEL and out["hook"] == "from ollama" and ollama == [config.OLLAMA_MODEL]
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["error"] == "overloaded" and row["cost_usd"] is None


def test_daily_cap_switches_to_ollama_without_calling_claude(conn, monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_DAILY_CAP", 1)
    sent, ollama = [], []
    fake_claude(monkeypatch, sent=sent)
    fake_ollama(monkeypatch, ollama)
    llm.generate(FACTS["new_tour"], conn=conn)
    out = llm.generate(FACTS["new_tour"], conn=conn)
    assert len(sent) == 1 and out["model"] == config.OLLAMA_MODEL


def test_templated_hook_only_drafts_label_the_model(conn, monkeypatch):
    fake_claude(monkeypatch, reply={"hook": "Presale alert: Nova Vale", "hashtags": []})
    out = llm.generate(FACTS["presale"], script="Python wrote this.", caption="Set a reminder.", conn=conn)
    assert out["model"] == "template + claude-haiku-5-5" and out["script"] == "Python wrote this."


def test_without_a_key_or_conn_it_is_ollama(conn, monkeypatch):
    ollama = []
    fake_ollama(monkeypatch, ollama)
    assert llm.generate(FACTS["new_tour"])["model"] == config.OLLAMA_MODEL  # no conn: no cap check, no Claude
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert not llm.uses_claude() and llm.generate(FACTS["new_tour"], conn=conn)["model"] == config.OLLAMA_MODEL
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
