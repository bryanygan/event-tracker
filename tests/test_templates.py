"""Templated scripts for presale/status alerts, and how drafts use them."""
import json

import responses

from tracker import config, db, detectors, drafts, llm, templates

PRESALE = {
    "format": "presale",
    "artist": "Zach Bryan",
    "sale_name": "Eagles Presale",
    "sale_opens": "Thu, Oct 22 at 10:00 AM ET",
    "all_sales": [
        {"name": "Eagles Presale", "opens": "Thu, Oct 22 at 10:00 AM ET"},
        {"name": "Radio Presale", "opens": "Thu, Oct 22 at 10:00 AM ET"},
        {"name": "General public on-sale", "opens": "Fri, Oct 23 at 10:00 AM ET"},
    ],
    "shows": [{"artist": "Zach Bryan", "date": "Tue, May 25", "venue": "Lincoln Financial Field"}],
}


def test_presale_script_states_every_window_and_show():
    s = templates.presale_script(PRESALE)
    assert "The Eagles and Radio presales for Zach Bryan open Thursday, October 22 at 10 AM Eastern." in s
    assert "General on-sale is Friday, October 23 at 10 AM Eastern." in s
    assert "Tuesday, May 25 show at Lincoln Financial Field" in s
    assert "codes" in s
    assert llm.check_facts({"script": s}, PRESALE) == []  # spoken form still matches the facts


def test_presale_script_general_only_has_no_code_line_and_combines_same_time():
    f = {**PRESALE, "sale_name": "General public on-sale",
         "all_sales": [{"name": "General public on-sale", "opens": "Fri, Oct 23 at 10:00 AM ET"}]}
    s = templates.presale_script(f)
    assert "Zach Bryan tickets go on sale to everyone Friday, October 23" in s and "code" not in s

    f = {**PRESALE, "all_sales": [{"name": "Citi® Preferred", "opens": "Fri, Oct 23 at 10:00 AM ET"},
                                  {"name": "General public on-sale", "opens": "Fri, Oct 23 at 10:00 AM ET"}]}
    assert "The Citi Preferred and the general on-sale for Zach Bryan open Friday" in templates.presale_script(f)


def test_multi_night_and_pretty_names():
    f = {**PRESALE, "all_sales": [{"name": "LIVE NATION PRESALE", "opens": "Thu, Oct 22 at 10:00 AM ET"}],
         "shows": [{"date": "Sat, Nov 7", "venue": "Xfinity Mobile Arena"},
                   {"date": "Sun, Nov 8", "venue": "Xfinity Mobile Arena"}]}
    s = templates.presale_script(f)
    assert "The Live Nation Presale for Zach Bryan opens" in s
    assert "their Saturday, November 7 and Sunday, November 8 shows at Xfinity Mobile Arena" in s


def test_status_change_script():
    s = templates.status_change_script({"artist": "Star", "change": "cancelled",
                                        "show": {"date": "Sat, Nov 7", "venue": "The Fillmore Philadelphia"}})
    assert "Their Saturday, November 7 show at The Fillmore Philadelphia has been cancelled." in s
    assert "Refunds" in s


def test_weekend_is_not_templated():
    assert templates.script_for({"format": "weekend", "shows": []}) is None


def test_caption_for_presale_pairs_each_time_with_its_sale():
    c = templates.caption_for(PRESALE)
    assert c.startswith("Zach Bryan tickets 🎟️ Eagles Presale: Thu, Oct 22 at 10:00 AM ET.")
    assert "General on-sale: Fri, Oct 23 at 10:00 AM ET." in c
    assert "Show: Tue, May 25 at Lincoln Financial Field." in c
    assert llm.check_facts({"caption": c}, PRESALE) == []


@responses.activate
def test_generate_with_template_asks_only_for_hook_and_hashtags():
    content = {"hook": "Zach Bryan presale Thursday", "hashtags": ["#country", "#ZachBryan"]}
    responses.post(f"{config.OLLAMA_URL}/api/chat", json={"message": {"content": json.dumps(content)}})
    out = llm.generate(PRESALE, model="m", script="SCRIPT TEXT", caption="CAPTION TEXT")
    body = json.loads(responses.calls[0].request.body)
    assert body["format"]["required"] == ["hook", "hashtags"]
    assert "SCRIPT: SCRIPT TEXT" in body["messages"][1]["content"]
    assert (out["script"], out["caption"], out["model"]) == ("SCRIPT TEXT", "CAPTION TEXT", "template + m")
    assert out["hashtags"] == ["#philly", "#country", "#ZachBryan"]


def test_draft_replaces_flagged_hook_with_fallback(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    detectors.save_alerts(conn, [{"key": "k", "kind": "presale_soon", "title": "t", "score": 1, "facts": PRESALE}])
    bad = {"hook": "Zach Bryan presale opens Tue, May 26", "hashtags": ["#philly"], "model": "m"}
    monkeypatch.setattr(llm, "generate",
                        lambda facts, model=None, script=None, caption=None: {**bad, "script": script, "caption": caption})
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, 1),)).fetchone()
    assert d["hook"] == "Set an alarm: Zach Bryan tickets drop Thu"
    assert d["caption"].startswith("Zach Bryan tickets 🎟️ Eagles Presale: Thu, Oct 22")
    assert d["script"].startswith(("Heads up", "Philly, set", "Quick ticket"))
    assert json.loads(d["warnings"]) == []


def test_hook_missing_exact_artist_name_falls_back(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    f = {**PRESALE, "artist": "THROWN"}
    detectors.save_alerts(conn, [{"key": "k", "kind": "presale_soon", "title": "t", "score": 1, "facts": f}])
    monkeypatch.setattr(llm, "generate", lambda facts, model=None, script=None, caption=None:
                        {"hook": "THROW tickets drop Fri", "hashtags": [], "model": "m", "script": script, "caption": caption})
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, 1),)).fetchone()
    assert d["hook"] == "Set an alarm: THROWN tickets drop Thu"


def test_venue_hashtag_typos_are_fixed():
    tags = llm.clean_hashtags(["#thefillmophila", "#thetheatortolivingarts"],
                              {"shows": [{"venue": "The Fillmore Philadelphia"}, {"venue": "Theatre of Living Arts"}]})
    assert tags[1:] == ["#thefillmophila", "#TheatreofLivingArts"]
