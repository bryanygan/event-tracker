"""The dashboard's draft progress bar: the job says which alert it's for, what step it's on and about how long."""
import time
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from tracker import api, db, detectors, llm

from tests.test_angles import FACTS


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    monkeypatch.setattr(api, "_gen_seconds", {"ollama": 40.0, "claude": 8.0})
    c = db.connect(path)
    detectors.save_alerts(c, [{"key": "p", "kind": "presale_soon", "title": "t", "score": 50, "facts": FACTS["presale"]},
                              {"key": "n", "kind": "new_tour", "title": "t", "score": 50, "facts": FACTS["new_tour"]}])
    c.close()
    return TestClient(api.app)


def wait_for_job():
    for _ in range(200):
        if not api._job_lock.locked():
            return
        time.sleep(0.02)
    raise AssertionError("job never finished")


def fake_model(monkeypatch, outputs, seen):
    """generate() returns the given drafts in order and notes the job's step/estimate when called."""
    outputs = iter(outputs)
    monkeypatch.setattr(llm, "ollama_up", lambda: True)
    monkeypatch.setattr(llm, "ollama_session", nullcontext)

    def generate(facts, **kw):
        seen.append((api._job["step"], api._job["estimate"]))
        return {"model": "fake", "hashtags": [], **next(outputs)}
    monkeypatch.setattr(llm, "generate", generate)


def test_templated_angle_draft_reports_alert_and_finishes(client):
    started = client.post("/api/alerts/1/draft", params={"angle": "news"}).json()
    assert started["running"] is True and started["alert_id"] == 1 and started["estimate"] == 2
    wait_for_job()
    job = client.get("/api/job").json()
    assert not job["running"] and job["error"] is None and job["elapsed"] is None
    assert job["step"] == "Saving the draft"
    assert len(client.get("/api/alerts").json()[0]["drafts"]) == 1


def test_model_draft_estimate_grows_on_retry_and_learns_from_clean_runs(client, monkeypatch):
    bad = {"hook": "Nova Vale Sat Dec 12", "script": "Nova Vale plays Sat, Dec 12.", "caption": "Dec 12"}
    good = {"hook": "Nova Vale is coming", "script": "Nova Vale plays Union Transfer.", "caption": "Nova Vale"}
    seen = []
    fake_model(monkeypatch, [bad, good], seen)
    started = client.post("/api/alerts/2/draft").json()
    assert started["estimate"] == 40 and started["step"] == "Writing the draft"
    wait_for_job()
    assert seen[0] == ("Writing the draft", 40)
    assert seen[1] == ("Second try: the first draft didn't match the facts", 80)  # one more generation's worth
    assert api._gen_seconds["ollama"] == 40.0  # a retried job isn't a fair sample

    seen.clear()
    fake_model(monkeypatch, [good], seen)
    client.post("/api/alerts/2/draft")
    wait_for_job()
    assert seen == [("Writing the draft", 40)]
    assert api._gen_seconds["ollama"] == 2.0  # learned (the fake model is instant; clamped to the 2 s floor)
    assert client.get("/api/job").json()["error"] is None


def test_cold_start_adds_ollama_start_time(client, monkeypatch):
    monkeypatch.setattr(llm, "ollama_up", lambda: False)
    monkeypatch.setattr(api, "_start_job", lambda name, fn, **kw: kw)  # don't actually run it
    out = client.post("/api/alerts/2/draft").json()
    assert out == {"alert_id": 2, "step": "Starting the model", "estimate": 40 + api.OLLAMA_START_SECONDS}
