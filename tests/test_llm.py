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
