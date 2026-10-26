"""New draft with notes (tracker/redraft.py): your note steers the model, shows you add come from the data,
shows you leave out are dropped, and a show leaving an idea says why."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tracker import api, db, detectors, drafts, llm, redraft, slides, store
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event

TODAY = date(2026, 10, 21)
DRAFT = {"hook": "November is unreal", "script": "Six dance shows.", "caption": "Who's going?", "hashtags": ["#philly"],
         "model": "test"}


def ingest(conn, raws):
    run = store.start_run(conn, "tm")
    store.upsert_events(conn, run, [tm.normalize(r) for r in raws], baseline=True)
    store.finish_run(conn, run, fetched=len(raws), api_calls=0, ok=True)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(redraft, "today_et", lambda: TODAY)
    c = db.connect(tmp_path / "r.db")
    # Ticketmaster files Glass Hours as Pop; Deezer says Dance/Electronic (same family, so Pop stands)
    ingest(c, [tm_event(event_id=f"d{i}", name=f"Echo Bay {i}", genre="Dance/Electronic", date=f"2026-11-{10 + i}",
                        venue="The Met Philadelphia", upcoming=50) for i in range(6)]
           + [tm_event(event_id="fc", name="Glass Hours", genre="Pop", date="2026-11-17", venue="Union Transfer",
                       upcoming=50)])
    c.execute("UPDATE attractions SET deezer_genre = 'Dance/Electronic' WHERE name = 'Glass Hours'")
    [alert] = detectors.detect_stacked_months(c, TODAY)
    detectors.save_alerts(c, [alert])
    c.commit()
    return c


def alert_row(conn):
    return dict(conn.execute("SELECT * FROM alerts WHERE key LIKE 'stacked:%'").fetchone())


def test_candidates_include_a_neighbouring_genre_and_say_why_it_was_left_out(conn):
    a = alert_row(conn)
    [cand] = redraft.candidates(conn, a, today=TODAY)
    assert cand["id"] == "fc" and cand["artist"] == "Glass Hours" and cand["date"] == "Tue, Nov 17"
    assert cand["why_not"] == ("listed as Pop (the ticket listing; Deezer says Dance/Electronic), "
                               "not Dance/Electronic")
    assert redraft.candidates(conn, a, q="glass", today=TODAY)[0]["id"] == "fc"
    assert redraft.candidates(conn, a, q="nobody", today=TODAY) == []


def test_draft_with_an_added_show_and_a_note(conn, monkeypatch):
    sent = {}

    def fake_generate(facts, **kw):
        sent.update(facts=facts, **kw)
        return dict(DRAFT)
    monkeypatch.setattr(llm, "generate", fake_generate)
    a = alert_row(conn)
    facts = json.loads(a["facts"])
    ins = redraft.validate("Lead with Glass Hours, keep it short", ["fc"], [], facts)
    draft_id = drafts.draft_alert(conn, a["id"], instructions=ins)
    assert sent["note"] == "Lead with Glass Hours, keep it short"
    names = [s["artist"] for s in sent["facts"]["shows"]]
    assert "Glass Hours" in names and names.index("Glass Hours") == 6  # Nov 17, after the Nov 10-15 shows
    assert sent["facts"]["notable_show_count"] == 7
    row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    saved = json.loads(row["instructions"])
    assert saved["added"] == ["Glass Hours (Tue, Nov 17)"] and saved["left_out"] == []
    assert any("Glass Hours" in line for line in json.loads(row["onscreen"]))
    # the idea itself still leaves Glass Hours out: that isn't reported as a change since the draft
    d = drafts.draft_row(row, facts, conn)
    assert d["facts_changes"] == [] and d["instructions"]["note"].startswith("Lead with")
    # the slides list the same shows as the script
    assert "Glass Hours" in json.dumps(slides.deck(conn, a["id"])["manifest"])


def test_leaving_a_show_out(conn, monkeypatch):
    sent = {}
    monkeypatch.setattr(llm, "generate", lambda facts, **kw: sent.update(facts=facts) or dict(DRAFT))
    a = alert_row(conn)
    facts = json.loads(a["facts"])
    key = redraft.show_key(facts["shows"][0])
    drafts.draft_alert(conn, a["id"], instructions=redraft.validate(None, [], [key], facts))
    assert key not in {redraft.show_key(s) for s in sent["facts"]["shows"]}
    assert sent["facts"]["notable_show_count"] == 5
    with pytest.raises(redraft.RedraftError, match="at least one"):
        redraft.validate(None, [], [redraft.show_key(s) for s in facts["shows"]], facts)
    with pytest.raises(redraft.RedraftError, match="Not in this idea"):
        redraft.validate(None, [], ["Nobody|Mon, Nov 2"], facts)
    with pytest.raises(redraft.RedraftError, match="no list of shows"):
        redraft.validate(None, ["fc"], [], {"format": "presale", "show": {}})
    assert redraft.validate("  ", [], [], facts) is None


def test_a_note_naming_a_missing_act_says_how_to_add_them(conn, monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda facts, **kw: dict(DRAFT))
    a = alert_row(conn)
    draft_id = drafts.draft_alert(conn, a["id"], instructions={"note": "include glass hours please", "add": [], "drop": []})
    warnings = json.loads(conn.execute("SELECT warnings FROM drafts WHERE id=?", (draft_id,)).fetchone()[0])
    assert any("Glass Hours" in w and "Add a show" in w for w in warnings)


def test_a_removed_show_says_why(conn):
    a = alert_row(conn)
    facts = json.loads(a["facts"])
    glass = detectors.event_facts(next(e for e in detectors.upcoming_events(conn, TODAY.isoformat()) if e["id"] == "fc"))
    earlier = {**facts, "shows": facts["shows"] + [glass], "notable_show_count": 7}
    lines = redraft.explain(detectors.facts_diff(earlier, facts), redraft.removal_reasons(conn, earlier, facts, TODAY))
    assert "Removed: Glass Hours (Tue, Nov 17): listed as Pop (the ticket listing; Deezer says Dance/Electronic), " \
           "not Dance/Electronic" in lines
    assert "notable_show_count: 7 → 6" in lines
    conn.execute("UPDATE events SET active = 0 WHERE id = 'fc'")
    assert redraft.removal_reasons(conn, earlier, facts, TODAY) == {
        "Glass Hours (Tue, Nov 17)": "no longer listed by Ticketmaster or the venue"}


def test_api(conn, tmp_path, monkeypatch):
    path = tmp_path / "r.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    jobs = []
    monkeypatch.setattr(api, "_start_job", lambda name, fn, **kw: jobs.append(name) or {"name": name})
    client = TestClient(api.app)
    a = alert_row(conn)
    assert client.get(f"/api/alerts/{a['id']}/candidates").json()[0]["id"] == "fc"
    r = client.post(f"/api/alerts/{a['id']}/draft", json={"note": "more hype", "add": ["fc"]})
    assert r.status_code == 202 and jobs == [f"draft for alert {a['id']} with your notes"]
    assert client.post(f"/api/alerts/{a['id']}/draft", json={"drop": ["Nobody|x"]}).status_code == 400
    assert client.post(f"/api/alerts/{a['id']}/draft").status_code == 202  # a plain new draft still works
