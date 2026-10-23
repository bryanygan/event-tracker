"""Music news M5: story scores, feedback buttons, Send to queue (tracker/music.py)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, config, db, music, opportunities, posts, store, stories, watchlist
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event
from tests.test_stories import T0, art, build

NOW = T0 + timedelta(hours=6)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    for name in ("Nova Vale", "Kilo Moon", "Iron Lark"):
        watchlist.add(c, name, "core")
    yield c
    c.close()


def story(conn, title):
    return music._story(conn, conn.execute("SELECT id FROM stories WHERE title=?", (title,)).fetchone()["id"])


def scored(conn, title, now=NOW):
    return music.evaluate(story(conn, title), music.context(conn, now))


def comp(result, key):
    return next(c for c in result["components"] if c["key"] == key)


def artist_id(conn, name):
    return conn.execute("SELECT id FROM artist_identities WHERE name=?", (name,)).fetchone()["id"]


def test_the_kind_of_story_matters_more_than_anything_else(conn):
    art(conn, "Nova Vale Teams Up With Lego for a Collector Set")
    art(conn, "Kilo Moon Debuts at No. 1 on the Billboard 200")
    build(conn)
    brand, chart = scored(conn, "Nova Vale Teams Up With Lego for a Collector Set"), \
        scored(conn, "Kilo Moon Debuts at No. 1 on the Billboard 200")
    assert comp(brand, "content")["value"] == 90 and comp(chart, "content")["value"] == 30
    assert brand["score"] > chart["score"]
    assert brand["angle"].startswith("Lead with the tie-in")
    # Momentum is unknown (M7) and left out, not counted as zero
    assert comp(brand, "momentum")["value"] is None and brand["coverage"] < 1
    assert brand["rules_version"] == config.MUSIC_RULES_VERSION


def test_politics_is_held_down_unless_you_mark_it_interested(conn, monkeypatch):
    monkeypatch.setitem(config.MUSIC_WEIGHTS, "content", 0.0)  # so the cap, not the content value, decides
    art(conn, "Nova Vale Mocks the Election in New Freestyle", source="feed-a")
    art(conn, "Nova Vale Mocks the Election in New Freestyle", source="feed-b", hours=1)
    build(conn)
    s = scored(conn, "Nova Vale Mocks the Election in New Freestyle")
    assert s["score"] == config.MUSIC_POLITICS_CAP and any("Politics" in c for c in s["caveats"])
    music.set_feedback(conn, story(conn, "Nova Vale Mocks the Election in New Freestyle")["id"], "interested", NOW)
    assert scored(conn, "Nova Vale Mocks the Election in New Freestyle")["score"] > config.MUSIC_POLITICS_CAP


def test_a_tour_by_someone_you_have_seen_live_with_a_philly_date(conn):
    art(conn, "Nova Vale Announces 2027 World Tour")
    art(conn, "Kilo Moon Announces 2027 World Tour", hours=1)
    build(conn)
    plain = scored(conn, "Kilo Moon Announces 2027 World Tour")
    assert comp(plain, "content")["value"] == 75 and plain["angle"].startswith("What to expect")

    watchlist.update(conn, artist_id(conn, "Nova Vale"), {"seen_live": True})
    r = store.start_run(conn, "ticketmaster")
    store.upsert_events(conn, r, [tm.normalize(tm_event(name="Nova Vale", date="2026-11-08"))], baseline=True)
    store.finish_run(conn, r, fetched=1, api_calls=0, ok=True)
    s = scored(conn, "Nova Vale Announces 2027 World Tour")
    assert comp(s, "content")["value"] == 100
    assert "seen live" in comp(s, "content")["why"] and "Philly date" in comp(s, "content")["why"]
    assert s["philly_shows"][0]["venue"] == "The Fillmore Philadelphia"
    assert s["angle"].startswith("You've seen them live")


def test_urgency_freshness_and_release_dates(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours, Out October 22")
    art(conn, "Kilo Moon Shares New Song “Static”")
    build(conn)
    soon = scored(conn, "Nova Vale Announces New Album Glass Hours, Out October 22", now=T0 + timedelta(days=2))
    assert (comp(soon, "urgency")["value"], comp(soon, "urgency")["why"]) == (100, "Out today")  # news 48h old: 65
    later = scored(conn, "Kilo Moon Shares New Song “Static”", now=T0 + timedelta(days=3))
    assert comp(later, "urgency")["value"] == 40  # 72 hours old


def test_feedback_teaches_the_artist_and_the_kind_not_the_story_itself(conn):
    art(conn, "Nova Vale Shares New Song “Static”")
    art(conn, "Nova Vale Shares New Song “Night Drive”", hours=80)  # a separate story (outside 72 h)
    art(conn, "Kilo Moon Shares New Song “Echo Bay”")
    build(conn, T0 + timedelta(days=4))
    now = T0 + timedelta(days=4)
    first, second = story(conn, "Nova Vale Shares New Song “Static”"), story(conn, "Nova Vale Shares New Song “Night Drive”")
    before = comp(scored(conn, second["title"], now), "relevance")["value"]
    own_before = comp(scored(conn, first["title"], now), "relevance")["value"]
    other_before = comp(scored(conn, "Kilo Moon Shares New Song “Echo Bay”", now), "relevance")["value"]

    music.set_feedback(conn, first["id"], "not_my_lane", now)
    after = comp(scored(conn, second["title"], now), "relevance")
    assert after["value"] == before - 25 - 10  # the artist and the kind of story
    assert "Not my lane" in after["why"]
    assert comp(scored(conn, first["title"], now), "relevance")["value"] == own_before  # its own button doesn't count
    assert comp(scored(conn, "Kilo Moon Shares New Song “Echo Bay”", now), "relevance")["value"] == other_before - 10

    music.set_feedback(conn, first["id"], None, now)
    assert comp(scored(conn, second["title"], now), "relevance")["value"] == before
    assert [r["action"] for r in conn.execute("SELECT action FROM story_actions ORDER BY id")] == ["not_my_lane", "cleared"]
    with pytest.raises(music.MusicError):
        music.set_feedback(conn, first["id"], "love_it", now)


def test_already_covered_and_not_my_lane_sink_in_for_you():
    rows = [{"id": 1, "score": 90, "feedback": "already_covered", "alert_id": None},
            {"id": 2, "score": 50, "feedback": None, "alert_id": None},
            {"id": 3, "score": 70, "feedback": None, "alert_id": 5},
            {"id": 4, "score": 60, "feedback": "interested", "alert_id": None}]
    assert [r["id"] for r in sorted(rows, key=music.rank_key)] == [4, 2, 3, 1]


def test_conflicting_dates_cap_the_score(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours, Out October 24", source="feed-a")
    art(conn, "Nova Vale Sets New Album Glass Hours for November 14", source="feed-b", hours=1)
    build(conn)
    s = scored(conn, "Nova Vale Announces New Album Glass Hours, Out October 24")
    assert comp(s, "confidence")["value"] == 25
    assert s["score"] <= config.MUSIC_CONFLICT_CAP and any("disagree" in c for c in s["caveats"])


def test_one_source_says_research_needed_and_sensitive_says_so(conn):
    art(conn, "Nova Vale Sued Over Sample in New Single")
    build(conn)
    s = scored(conn, "Nova Vale Sued Over Sample in New Single")
    assert comp(s, "confidence")["value"] == 50
    assert any("Research needed" in c for c in s["caveats"]) and any("Sensitive" in c for c in s["caveats"])


def test_send_to_queue_builds_facts_from_evidence_only(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours, Out October 24", source="feed-a")
    art(conn, "Nova Vale and Kilo Moon Announce New Album Glass Hours", source="feed-b", hours=1)
    build(conn)
    s = story(conn, "Nova Vale Announces New Album Glass Hours, Out October 24")
    alert_id = music.send(conn, s["id"], NOW)
    assert music.send(conn, s["id"], NOW) == alert_id  # sending twice doesn't make a second idea
    a = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    facts = json.loads(a["facts"])
    assert a["kind"] == "music_story" and a["title"] == s["title"]
    assert facts["format"] == "music_story" and facts["artist"] == "Nova Vale" and facts["other_artists"] == ["Kilo Moon"]
    assert facts["release_title"] == "Glass Hours" and facts["release_date"] == "Sat, Oct 24"
    assert facts["release_date_from"] == ["Feed-A"]
    assert [x["outlet"] for x in facts["sources"]] == ["Feed-A", "Feed-B"]
    assert a["post_by"].startswith("2026-10-24T04:00")  # release day, midnight Eastern
    assert [r["action"] for r in conn.execute("SELECT action FROM story_actions")] == ["sent"]

    # The story keeps its alert link through a rebuild, and the queue scores it the music way
    build(conn)
    assert story(conn, s["title"])["alert_id"] == alert_id
    [item] = [i for i in opportunities.queue(conn, NOW) if i["alert_id"] == alert_id]
    assert item["pillar"] == "music" and item["lane"] == "music" and item["stage"] == "new"
    assert {c["key"] for c in item["components"]} == set(config.MUSIC_WEIGHTS)
    assert item["series"] == "Music news" and [l["label"] for l in item["links"]] == ["Feed-A", "Feed-B"]


def test_conflicting_dates_reach_the_facts_with_their_sources_and_no_single_date(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours, Out October 24", source="feed-a")
    art(conn, "Nova Vale Sets New Album Glass Hours for November 14", source="feed-b", hours=1)
    build(conn)
    facts = music.facts_for(conn, story(conn, "Nova Vale Announces New Album Glass Hours, Out October 24"),
                            music.context(conn, NOW))
    assert "release_date" not in facts
    assert facts["release_dates_reported"] == [{"date": "Sat, Oct 24", "from": "Feed-A"},
                                               {"date": "Sat, Nov 14", "from": "Feed-B"}]


def test_a_recent_post_about_the_artist_is_a_caveat(conn):
    art(conn, "Nova Vale Teams Up With Lego for a Collector Set")
    art(conn, "Nova Vale Announces 2027 World Tour", hours=1)
    build(conn)
    sent = music.send(conn, story(conn, "Nova Vale Teams Up With Lego for a Collector Set")["id"], NOW)
    posts.create_post(conn, alert_id=sent, posted_at=(NOW - timedelta(hours=1)).isoformat())
    tour = scored(conn, "Nova Vale Announces 2027 World Tour")
    assert any(c.startswith("You posted about Nova Vale") for c in tour["caveats"])


def test_auto_send_is_off_until_m9_and_never_sends_sensitive_stories(conn, monkeypatch):
    for src in ("feed-a", "feed-b"):
        art(conn, "Nova Vale Teams Up With Lego for a Collector Set", source=src)
        art(conn, "Kilo Moon Sued Over Lego Collector Set", source=src, hours=1)
    build(conn)
    assert music.auto_send(conn, NOW) == []
    monkeypatch.setattr(config, "MUSIC_AUTO_SEND", True)
    monkeypatch.setattr(config, "MUSIC_AUTO_SEND_MIN", 60)
    sent = music.auto_send(conn, NOW)
    assert len(sent) == 1
    assert conn.execute("SELECT title FROM alerts").fetchone()[0] == "Nova Vale Teams Up With Lego for a Collector Set"
    assert conn.execute("SELECT action FROM story_actions").fetchone()[0] == "auto_sent"


def test_api_scores_feedback_send_and_seen_live(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    for name in ("Nova Vale", "Kilo Moon"):
        watchlist.add(c, name, "core")
    now = datetime.now(timezone.utc)
    sid = c.execute("INSERT INTO news_sources (key, name, url) VALUES ('feed-a', 'Feed A', 'https://a.example')").lastrowid
    for i, title in enumerate(["Kilo Moon Debuts at No. 1 on the Billboard 200",
                               "Nova Vale Teams Up With Lego for a Collector Set"]):
        when = (now - timedelta(hours=2 - i)).replace(microsecond=0).isoformat()
        c.execute("""INSERT INTO articles (source_id, guid, url, title, published_at, fetched_at, title_hash)
                     VALUES (?,?,?,?,?,?,?)""", (sid, title, f"https://a.example/{i}", title, when, when, title))
    c.commit()
    watchlist.rematch(c)
    c.close()
    client = TestClient(api.app)

    got = client.get("/api/news/stories?days=1").json()
    assert [s["title"] for s in got] == ["Nova Vale Teams Up With Lego for a Collector Set",
                                         "Kilo Moon Debuts at No. 1 on the Billboard 200"]
    assert got[0]["score"] > got[1]["score"] and got[0]["angle"] and got[0]["band"]
    assert [s["title"] for s in client.get("/api/news/stories?days=1&sort=recent").json()] == [
        "Nova Vale Teams Up With Lego for a Collector Set", "Kilo Moon Debuts at No. 1 on the Billboard 200"]
    assert client.get("/api/news/stories?sort=hot").status_code == 400

    lego = got[0]["id"]
    assert client.post(f"/api/news/stories/{lego}/feedback", json={"feedback": "meh"}).status_code == 400
    assert client.post("/api/news/stories/999/feedback", json={"feedback": "interested"}).status_code == 404
    assert client.post(f"/api/news/stories/{lego}/feedback", json={"feedback": "already_covered"}).json()["ok"]
    assert [s["id"] for s in client.get("/api/news/stories?days=1").json()][-1] == lego  # handled: it sinks

    r = client.post(f"/api/news/stories/{lego}/send")
    assert r.status_code == 201
    alert_id = r.json()["alert_id"]
    q = client.get("/api/queue").json()["items"]
    assert [i["pillar"] for i in q if i["alert_id"] == alert_id] == ["music"]
    assert client.post(f"/api/alerts/{alert_id}/draft").status_code == 400  # drafting music comes with M6

    nova = next(w for w in client.get("/api/watchlist").json() if w["name"] == "Nova Vale")
    assert nova["seen_live"] is False
    assert client.patch(f"/api/watchlist/{nova['id']}", json={"seen_live": True}).json()["seen_live"] is True
