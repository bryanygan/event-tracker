"""Music news M6: drafting formats, status wording, music fact checks, roundups, slides, analytics
(tracker/music_formats.py and its hooks in llm.py, drafts.py, slides.py, posts.py, context.py)."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from tracker import catalog, config, context, db, drafts, llm, music, music_formats as mf, opportunities, posts, slides, watchlist
from tests.test_stories import T0, art, build

NOW = T0 + timedelta(hours=6)
THURSDAY = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    for name in ("Nova Vale", "Kilo Moon", "Iron Lark", "Soft Arcade"):
        watchlist.add(c, name, "core")
    yield c
    c.close()


def aid(conn, name):
    return conn.execute("SELECT id FROM artist_identities WHERE name=?", (name,)).fetchone()["id"]


def sent(conn, title):
    s = conn.execute("SELECT id FROM stories WHERE title=?", (title,)).fetchone()
    return music.send(conn, s["id"], NOW)


def facts_of(conn, alert_id):
    return json.loads(conn.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()["facts"])


def fake_model(monkeypatch, reply, prompts=None):
    def chat(conn, model, prompt, schema, system=llm.SYSTEM_PROMPT):
        if prompts is not None:
            prompts.append((prompt, schema, system))
        return dict(reply), "fake-model"
    monkeypatch.setattr(llm, "_chat", chat)


def test_status_phrases_say_exactly_how_sure_we_are():
    base = {"format": "music_story", "story_type": "album_announcement", "artist": "Nova Vale"}
    assert mf.status_phrase({**base, "release_status": "announced"}) == "announced, no date yet"
    assert mf.status_phrase({**base, "release_status": "announced", "release_date": "Fri, Nov 6"}) == "announced for Fri, Nov 6"
    assert mf.status_phrase({**base, "release_status": "rumored"}) == "rumored, not confirmed"
    assert mf.status_phrase({**base, "release_status": "teased"}) == "teased, not announced yet"
    assert mf.status_phrase({**base, "release_status": "released"}) == "out now"
    assert mf.status_phrase({**base, "release_status": "announced", "release_dates_reported": [{}]}).startswith(
        "announced, but the sources disagree")
    assert mf.status_phrase({**base, "story_type": "legal"}) is None
    # Hooks are built from facts: a rumor never reads as an announcement
    assert mf.angles_for({**base, "release_status": "rumored"})[0]["hook"] == "Is Nova Vale about to drop an album?"
    assert mf.angles_for({**base, "release_status": "announced"})[0]["hook"] == "Nova Vale just announced a new album"
    careful = mf.angles_for({**base, "story_type": "legal", "sensitive": True})
    assert [a["key"] for a in careful] == ["news"] and careful[0]["hook"] == "What we know about Nova Vale so far"
    assert mf.format_key({**base, "story_type": "collaboration"}) == "collaboration"
    assert mf.format_key({**base, "story_type": "chart"}) == "music_news"


def test_music_fact_check_catches_claims_the_status_does_not_support():
    facts = {"format": "music_story", "story_type": "album_announcement", "artist": "Nova Vale",
             "release_status": "announced", "release_title": "Glass Hours", "verification": "unverified",
             "sources": [{"outlet": "Feed-A", "headline": "Nova Vale Announces New Album Glass Hours"}]}
    clean = {"hook": "Nova Vale just announced a new album",
             "script": "According to Feed-A, Nova Vale announced a new album, “Glass Hours”. No date yet.",
             "caption": "New Nova Vale album incoming."}
    assert llm.check_facts(clean, facts) == []
    bad = {"hook": "Nova Vale's new album is out now",
           "script": "It drops Friday and it's already trending. The lead single “Static Bloom” is confirmed.",
           "caption": ""}
    w = " | ".join(llm.check_facts(bad, facts))
    assert "out now" in w and "drops Friday" in w and "trending" in w and "“Static Bloom”" in w
    assert "Doesn't say the release status" in w
    rumor = {**facts, "release_status": "rumored"}
    assert any("confirmed" in x for x in llm.check_facts({"hook": "", "script": "It's confirmed, a rumor says.",
                                                            "caption": ""}, rumor))


def test_a_music_story_drafts_with_its_status_your_take_and_no_philly_tag(conn, monkeypatch):
    art(conn, "Nova Vale Announces New Album Glass Hours", excerpt="The follow-up arrives with a lead single.")
    build(conn)
    alert_id = sent(conn, "Nova Vale Announces New Album Glass Hours")
    facts = facts_of(conn, alert_id)
    assert facts["sources"][0]["summary"] == "The follow-up arrives with a lead single."
    prompts = []
    fake_model(monkeypatch, {"hook": "Nova Vale just announced a new album",
                             "script": "According to Feed-A, Nova Vale announced “Glass Hours”. No date yet.",
                             "closer": "Are you ready for it?", "caption": "Nova Vale album news 👀",
                             "hashtags": ["#novavale", "#newmusic"]}, prompts)
    draft_id = drafts.draft_alert(conn, alert_id)
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    parts = d["script"].split("\n\n")
    assert parts[0].startswith("According to Feed-A") and parts[1].startswith("YOUR TAKE:") and parts[2] == "Are you ready for it?"
    assert json.loads(d["warnings"]) == []
    assert "#philly" not in d["hashtags"].lower() and "#novavale" in d["hashtags"].lower()
    assert json.loads(d["onscreen"])[:2] == ["ALBUM ANNOUNCED: Nova Vale", "“Glass Hours” · ANNOUNCED, NO DATE YET"]
    prompt, schema, system = prompts[0]
    assert system == mf.SYSTEM_PROMPT and "closer" in schema["properties"]
    assert "announced, no date yet" in prompt and "according to Feed-A" in prompt
    assert "https://" not in prompt and "your_plays" not in prompt and "story_id" not in prompt


def test_a_bad_model_hook_is_replaced_by_the_fact_built_one(conn, monkeypatch):
    art(conn, "Nova Vale Announces New Album Glass Hours")
    build(conn)
    alert_id = sent(conn, "Nova Vale Announces New Album Glass Hours")
    fake_model(monkeypatch, {"hook": "Glass Hours is out now", "script": "Nova Vale announced a new album.",
                             "closer": "Thoughts?", "caption": "", "hashtags": []})
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, alert_id),)).fetchone()
    assert d["hook"] == "Nova Vale just announced a new album"


def put_releases(conn, artist, rows, today=date(2026, 10, 22)):
    catalog.store(conn, aid(conn, artist), "deezer",
                  [{"provider_id": f"{artist}:{t}", "title": t, "record_type": k, "release_date": d,
                    "url": f"https://deezer.example/{t}"} for t, k, d in rows], today)
    conn.commit()


def test_new_music_friday_lists_the_weeks_releases_from_the_catalogs(conn):
    put_releases(conn, "Nova Vale", [("Glass Hours", "album", "2026-10-23"), ("Older One", "single", "2026-10-10")])
    put_releases(conn, "Kilo Moon", [("Static - Single", "single", "2026-10-21")])
    put_releases(conn, "Iron Lark", [("Night Drive (feat. Iron Lark)", "single", "2026-10-23")])  # a guest spot
    put_releases(conn, "Soft Arcade", [("Echo Bay", "ep", "2026-10-17")])
    assert mf.new_music_friday(conn, THURSDAY - timedelta(days=2)) is None  # Tuesday: not yet
    a = mf.new_music_friday(conn, THURSDAY)
    assert a["key"] == "nmf:2026-10-23" and a["kind"] == "new_music_friday"
    rel = a["facts"]["releases"]
    assert [(r["artist"], r["title"], r["type"]) for r in rel] == [
        ("Nova Vale", "Glass Hours", "album"), ("Soft Arcade", "Echo Bay", "ep"), ("Kilo Moon", "Static", "single")]
    assert rel[0]["from"] == ["deezer"] and a["facts"]["week"] == "Fri, Oct 23"
    assert not any(k.startswith("_") for r in rel for k in r)  # ranking details (your plays) stay out of the facts

    [alert_id] = mf.roundups(conn, THURSDAY)
    assert mf.roundups(conn, THURSDAY) == []  # refreshed, not duplicated
    did = drafts.draft_alert(conn, alert_id, angle="news")  # templated: no model
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
    assert d["model"] == "template" and d["hook"] == "New Music Friday: 3 releases worth a listen"
    assert "Nova Vale, “Glass Hours”, a new album." in d["script"] and "YOUR TAKE:" in d["script"]
    assert json.loads(d["warnings"]) == [] and "#newmusicfriday" in d["hashtags"]
    item = next(i for i in opportunities.queue(conn, THURSDAY) if i["alert_id"] == alert_id)
    assert item["pillar"] == "music" and item["series"] == "New Music Friday" and item["lane"] == "music"
    assert item["reasons"][0].startswith("3 releases this week")
    deck = slides.build(facts_of(conn, alert_id))
    assert [s["kind"] for s in deck["slides"]] == ["title", "show", "show", "show", "cta"]
    assert deck["slides"][1]["fields"] == {"date": "ALBUM", "time": "", "artist": "Nova Vale", "venue": "“Glass Hours”",
                                          "extra": ""}


def test_upcoming_albums_from_preorders_and_dated_announcements(conn):
    for artist, title, day in [("Nova Vale", "Glass Hours", "2026-11-06"), ("Kilo Moon", "Static", "2026-11-13"),
                               ("Iron Lark", "Night Drive", "2026-11-20")]:
        put_releases(conn, artist, [(title, "album", day)])
    put_releases(conn, "Soft Arcade", [("Echo Bay - Single", "single", "2026-11-02")])  # singles aren't albums
    assert mf.upcoming_albums(conn, datetime(2026, 10, 10, 16, tzinfo=timezone.utc)) is None  # before the 15th
    a = mf.upcoming_albums(conn, THURSDAY)
    assert a["key"] == "upcoming:2026-11" and a["facts"]["month"] == "November 2026"
    assert [x["title"] for x in a["facts"]["albums"]] == ["Glass Hours", "Static", "Night Drive"]
    script = mf.script_for(a["facts"])
    assert "Nova Vale, “Glass Hours”, out Friday, November 6." in script
    assert llm.check_facts({"hook": mf.fallback_hook(a["facts"]), "script": script,
                            "caption": mf.caption_for(a["facts"])}, a["facts"]) == []


def test_slides_for_a_story_with_conflicting_dates_and_a_philly_show():
    facts = {"format": "music_story", "story_type": "album_announcement", "artist": "Nova Vale",
             "release_title": "Glass Hours", "release_status": "announced",
             "release_dates_reported": [{"date": "Fri, Nov 6", "from": "Feed-A"}, {"date": "Fri, Nov 13", "from": "Feed-B"}],
             "philly_shows": [{"date": "Sun, Nov 8", "venue": "The Fillmore Philadelphia"}],
             "sources": [{"outlet": "Feed-A"}, {"outlet": "Feed-B"}]}
    deck = slides.build(facts)
    title, release, philly, cta = deck["slides"]
    assert title["fields"]["kicker"] == "ALBUM ANNOUNCED" and title["fields"]["note"] == "via Feed-A, Feed-B"
    assert release["fields"]["date"] == "DATE NOT SETTLED"
    assert release["fields"]["extra"] == "Fri, Nov 6 per Feed-A · Fri, Nov 13 per Feed-B"
    assert philly["fields"]["lines"] == "Sun, Nov 8: The Fillmore Philadelphia"
    assert slides.validate(deck, {"release": {"extra": "my edit"}}) == {"release": {"extra": "my edit"}}


def test_analytics_split_by_pillar_and_music_format(conn):
    art(conn, "Nova Vale and Kilo Moon Link Up on New Single Static")
    build(conn)
    music_alert = sent(conn, "Nova Vale and Kilo Moon Link Up on New Single Static")
    posts.create_post(conn, alert_id=music_alert, posted_at="2026-10-20T20:00:00Z")
    posts.create_post(conn, title="A weekend video", format="weekend", posted_at="2026-10-19T20:00:00Z")
    by_pillar = {g["group"]: g["posts"] for g in posts.analytics(conn, "pillar")["groups"]}
    assert by_pillar == {"events": 1, "music": 1}
    by_format = {g["group"]: g["posts"] for g in posts.analytics(conn, "music_format")["groups"]}
    assert by_format == {"collaboration": 1}  # music posts only, by the story's kind
    with pytest.raises(posts.PostError):
        posts.analytics(conn, "mood")


def test_web_context_for_a_music_story_asks_about_the_story(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours")
    build(conn)
    facts = facts_of(conn, sent(conn, "Nova Vale Announces New Album Glass Hours"))
    assert context.subject(facts) == f"music:{facts['story_id']}"
    prompt = context.build_prompt(facts, NOW)
    assert "Nova Vale Announces New Album Glass Hours" in prompt and "Feed-A" in prompt and "previous projects" in prompt
