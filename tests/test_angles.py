"""Angles: deterministic hooks that pass the fact checker, and drafting from a chosen angle."""
import json

import pytest
from fastapi.testclient import TestClient

from tracker import angles, api, db, detectors, drafts, llm, templates

SHOW = {"artist": "Nova Vale", "date": "Sat, Nov 7", "time": "8:00 PM", "venue": "Union Transfer", "url": "u"}
FACTS = {
    "presale": {"format": "presale", "artist": "Nova Vale", "sale_name": "Artist Presale",
                "sale_opens": "Thu, Oct 22 at 10:00 AM ET", "hours_until_open": 18,
                "all_sales": [{"name": "Artist Presale", "opens": "Thu, Oct 22 at 10:00 AM ET"},
                              {"name": "General public on-sale", "opens": "Fri, Oct 23 at 10:00 AM ET"}],
                "shows": [SHOW],
                "history": {"is_comeback": True, "last_philly_show_year": 2017, "years_since_last_philly_show": 9,
                            "last_philly_show": "May 2, 2017", "last_philly_venue": "TLA"}},
    "new_tour": {"format": "new_tour", "artist": "Nova Vale", "already_had_philly_dates": False, "shows": [SHOW],
                 "public_onsale": "Fri, Oct 23 at 10:00 AM ET"},
    "weekend": {"format": "weekend", "weekend": "Fri, Oct 23 – Sun, Oct 25", "shows": [SHOW, {**SHOW, "artist": "Kilo Moon"}]},
    "stacked_month": {"format": "stacked_month", "month": "November 2026", "genre": "R&B", "notable_show_count": 7,
                      "shows": [SHOW]},
    "status_change": {"format": "status_change", "artist": "Nova Vale", "change": "postponed", "show": SHOW},
    "team_week": {"format": "team_week", "artist": "Sixers", "team": "Philadelphia 76ers", "week": "Mon, Oct 19 – Sun, Oct 25",
                  "shows": [{"artist": "Sixers vs. Bucks", "date": "Sat, Oct 24", "opponent": "Milwaukee Bucks", "kind": "game"}]},
    "big_game": {"format": "big_game", "artist": "Sixers", "team": "Philadelphia 76ers", "opponent": "Boston Celtics",
                 "show": {"artist": "Sixers vs. Celtics", "date": "Sat, Oct 24", "time": "7:30 PM", "kind": "game"}},
}


@pytest.mark.parametrize("fmt", FACTS)
def test_every_angle_hook_is_short_and_fact_clean(fmt):
    out = angles.angles_for(FACTS[fmt])
    assert len(out) >= 2 and len({a["key"] for a in out}) == len(out)
    for a in out:
        assert llm.check_facts({"hook": a["hook"]}, FACTS[fmt]) == [], a["hook"]
        assert len(a["hook"].split()) <= templates.MAX_HOOK_WORDS
        assert a["pitch"] and a["style"] and a["cta"]


def test_comeback_angle_only_with_history():
    assert angles.angles_for(FACTS["presale"])[0]["hook"] == "Nova Vale hasn't played Philly since 2017"
    plain = {**FACTS["presale"], "history": None}
    assert "comeback" not in [a["key"] for a in angles.angles_for(plain)]


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    for i, fmt in enumerate(("presale", "weekend"), 1):
        detectors.save_alerts(c, [{"key": f"k{i}", "kind": fmt, "title": "t", "score": 50, "facts": FACTS[fmt]}])
    return c


def test_templated_format_with_angle_needs_no_model(conn, monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda *a, **k: pytest.fail("model called"))
    assert not drafts.needs_llm(FACTS["presale"], "utility") and drafts.needs_llm(FACTS["presale"], None)
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, 1, angle="utility"),)).fetchone()
    assert d["hook"] == "Set an alarm: Thursday at 10 AM for Nova Vale" and d["angle"] == "utility"
    assert d["script"] == templates.presale_script(FACTS["presale"]) and d["model"] == "template"
    assert d["hashtags"].startswith("#philly") and "#NovaVale" in d["hashtags"]


def test_llm_format_keeps_the_chosen_hook_and_gets_the_angle(conn, monkeypatch):
    seen = {}

    def fake_generate(facts, model=None, script=None, caption=None, angle=None, conn=None):
        seen["angle"] = angle
        return {"hook": angle["hook"], "script": "Two shows this weekend.", "caption": "Save this.",
                "hashtags": ["#philly"], "model": "fake"}
    monkeypatch.setattr(llm, "generate", fake_generate)
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, 2, angle="fan"),)).fetchone()
    assert seen["angle"]["key"] == "fan" and d["hook"] == "Bored this weekend? Philly has plans for you"
    with pytest.raises(ValueError):
        drafts.draft_alert(conn, 2, angle="comeback")


def test_angle_prompt_fixes_the_hook(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": json.dumps({"hook": "ignored", "script": "s", "caption": "c", "hashtags": []})}}
    monkeypatch.setattr(llm.requests, "post", lambda url, timeout, json: sent.update(json) or Resp())
    a = angles.find(FACTS["weekend"], "news")
    out = llm.generate(FACTS["weekend"], angle=a)
    assert out["hook"] == "2 shows in Philly this weekend"
    assert 'The hook is already chosen: "2 shows in Philly this weekend"' in sent["messages"][1]["content"]


def test_api_lists_angles_and_rejects_unknown(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    detectors.save_alerts(c, [{"key": "k", "kind": "presale_soon", "title": "t", "score": 50, "facts": FACTS["presale"]}])
    c.close()
    client = TestClient(api.app)
    assert [a["key"] for a in client.get("/api/alerts").json()[0]["angles"]] == ["comeback", "news", "fan", "utility"]
    assert client.post("/api/alerts/1/draft", params={"angle": "nope"}).status_code == 400
