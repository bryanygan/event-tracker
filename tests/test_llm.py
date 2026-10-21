"""LLM helpers that don't need a running model."""
import json

import responses

from tracker import config, llm

FACTS = {
    "format": "weekend",
    "weekend": "Fri, Oct 23 – Sun, Oct 25",
    "shows": [
        {"artist": "Mt. Joy", "date": "Sat, Oct 24", "time": "8:00 PM", "venue": "The Met Philadelphia",
         "price": "$45–$85"},
        {"artist": "Rapper", "date": "Sun, Oct 25", "venue": "Union Transfer"},
    ],
}


def test_check_facts_accepts_rephrased_facts():
    draft = {"hook": "October 24th is gonna be wild", "script": "Mt. Joy at 8pm, tix from $45. 2 shows.",
             "caption": "Sun Oct 25 too!"}
    assert llm.check_facts(draft, FACTS) == []


def test_check_facts_flags_invented_details():
    draft = {"hook": "Oct 31 at 7pm!", "script": "Tickets just $30, use code PHILLY24", "caption": "12 shows"}
    warnings = llm.check_facts(draft, FACTS)
    for bad in ("Oct 31", "7pm", "$30", "12", "possible presale code"):
        assert any(bad in w for w in warnings), (bad, warnings)


def test_onscreen_lines_come_from_facts():
    lines = llm.onscreen_lines(FACTS)
    assert lines[0] == "PHILLY THIS WEEKEND · Fri, Oct 23 – Sun, Oct 25"
    assert lines[1] == "Sat, Oct 24 · Mt. Joy · The Met Philadelphia · $45–$85"


def test_build_prompt_fills_format_guide():
    p = llm.build_prompt({"format": "stacked_month", "month": "November 2026", "genre": "Country", "shows": []})
    assert "how stacked November 2026 is for Country" in p
    assert '"format"' not in p


@responses.activate
def test_generate_cleans_hashtags():
    content = {"hook": "h", "script": "s", "caption": "c", "hashtags": ["concerts", "#Live Music", " "]}
    responses.post(f"{config.OLLAMA_URL}/api/chat", json={"message": {"content": json.dumps(content)}})
    out = llm.generate(FACTS, model="m")
    assert out["hashtags"] == ["#philly", "#concerts", "#LiveMusic"]
    body = json.loads(responses.calls[0].request.body)
    assert body["format"]["required"] == ["hook", "script", "caption", "hashtags"]


def test_spoken_strips_hashtags_and_emoji():
    assert llm.spoken("Philly is LIT 🔥 #philly #concerts\nSaturday  at 8") == "Philly is LIT\nSaturday at 8"
    assert llm.spoken("way better 🔥!") == "way better!"


def test_check_facts_flags_leaked_placeholders():
    warnings = llm.check_facts({"hook": "[Artist] at [Venue]", "script": "", "caption": ""}, FACTS)
    assert "Not in facts: [Artist]" in warnings


def test_fill_placeholders_only_fills_real_fact_keys():
    facts = {"sale_opens": "Thu, Oct 22 at 10:00 AM ET", "shows": []}
    assert llm.fill_placeholders("Drops [sale_opens] for [Artist]", facts) == \
        "Drops Thu, Oct 22 at 10:00 AM ET for [Artist]"


def test_check_facts_flags_wrong_weekday_for_date():
    facts = {"shows": [{"date": "Sat, Nov 21", "artist": "Pouya"}]}
    assert llm.check_facts({"hook": "", "script": "Saturday, November 21st: Pouya", "caption": ""}, facts) == []
    warnings = llm.check_facts({"hook": "", "script": "Mon, Nov 21 - Pouya", "caption": ""}, facts)
    assert "Not in facts: Mon, Nov 21" in warnings


def test_fill_placeholders_is_case_insensitive():
    assert llm.fill_placeholders("[Artist] tickets", {"artist": "Zach Bryan"}) == "Zach Bryan tickets"


def test_clean_hashtags_fixes_artist_typos_and_punctuation():
    facts = {"artist": "Calvin Harris", "shows": [{"artist": "Calvin Harris"}]}
    tags = llm.clean_hashtags(["#calvinnharris", "#Rockin'Philly", "#philly", "#PHILLY", "#tiktok"], facts)
    assert tags == ["#CalvinHarris", "#RockinPhilly", "#philly", "#tiktok"]


def test_presale_prompt_for_general_onsale_says_no_code():
    p = llm.build_prompt({"format": "presale", "artist": "X", "sale_name": "General public on-sale",
                          "sale_opens": "Thu", "shows": []})
    assert "no code needed" in p


def test_prompt_labels_show_dates_distinctly():
    p = llm.build_prompt({"format": "presale", "artist": "X", "sale_name": "Presale", "sale_opens": "Thu, Oct 22",
                          "shows": [{"artist": "X", "date": "Tue, May 25", "time": "7:00 PM"}]})
    assert '"show_date": "Tue, May 25"' in p and '"show_time": "7:00 PM"' in p


def test_clean_hashtags_keeps_valid_variants():
    facts = {"shows": [{"artist": "A Boogie Wit Da Hoodie"}, {"artist": "Meek Mill"}, {"artist": "Doja Cat"}]}
    tags = llm.clean_hashtags(["#ABoogieWitheTheHoodie", "#MeekMillLive", "#DojaCatTour"], facts)
    assert tags == ["#philly", "#ABoogieWitDaHoodie", "#MeekMillLive", "#DojaCatTour"]
