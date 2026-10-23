"""Music stories M3: classification, release status, clustering, conflicts, and stable ids across rebuilds."""
from datetime import date, datetime, timedelta, timezone

import pytest

from tracker import db, stories, watchlist

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    for name in ("Nova Vale", "Kilo Moon", "Iron Lark"):
        watchlist.add(c, name, "core")
    yield c
    c.close()


def art(conn, title, source="feed-a", hours=0, excerpt=None):
    sid = conn.execute("SELECT id FROM news_sources WHERE key=?", (source,)).fetchone()
    sid = sid[0] if sid else conn.execute("INSERT INTO news_sources (key, name, url) VALUES (?, ?, ?)",
                                          (source, source.title(), f"https://{source}.example")).lastrowid
    from tracker.fetchers.news_rss import title_hash
    when = (T0 + timedelta(hours=hours)).isoformat()
    aid = conn.execute("""INSERT INTO articles (source_id, guid, url, title, excerpt, published_at, fetched_at, title_hash)
                          VALUES (?,?,?,?,?,?,?,?)""", (sid, f"{source}:{title}", f"https://{source}.example/{hours}",
                                                        title, excerpt, when, when, title_hash(title))).lastrowid
    conn.commit()
    return aid


def build(conn, now=None):
    watchlist.rematch(conn)  # tags, then stories.rebuild
    stories.rebuild(conn, now or T0 + timedelta(days=1))
    return [dict(r) | {"n": r["n"]} for r in conn.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM story_articles x WHERE x.story_id = s.id) AS n FROM stories s
           ORDER BY s.first_seen, s.id""")]


@pytest.mark.parametrize("title, expected", [
    ("Nova Vale Announces New Album Glass Hours", "album_announcement"),
    ("Nova Vale Shares New Song “Static”", "single_release"),
    ("Nova Vale Reveals Tracklist for Glass Hours", "tracklist"),
    ("Nova Vale Teases Something Big With Cryptic Post", "tease"),
    ("Nova Vale Announces 2027 World Tour", "tour"),
    ("Nova Vale Debuts at No. 1 on the Billboard 200", "chart"),
    ("Nova Vale Is Now a Lego Set", "brand_collab"),
    ("Nova Vale Sued Over Sample in Hit Song", "legal"),
    ("Nova Vale Accused of Stealing a Hook", "legal"),
    ("Nova Vale Hospitalized After Show", "sensitive"),
    ("Nova Vale Weighs In on the Election", "politics"),
    ("Nova Vale Celebrates Birthday With Lavish Party", "lifestyle"),
    ("Nova Vale's Debut Album Turns 10", "anniversary"),
    ("New Music Friday: Nova Vale, Kilo Moon and More", "roundup"),
    ("Kilo Moon Signs With a New Label", "artist_development"),
    ("Glass Hours – Song by Nova Vale", "single_release"),
    ("Nova Vale Sits Down for a Long Interview", "other"),
])
def test_classify(title, expected):
    assert stories.classify(title)[0] == expected


def test_classify_explains_itself_and_the_summary_never_makes_a_release():
    assert stories.classify("Nova Vale Announces 2027 Tour")[1] == "tour: 'Tour'"
    t, why = stories.classify("Nova Vale on fame", "She was arrested last year")
    assert (t, why) == ("legal", "legal: 'arrested' (summary)")
    assert stories.classify("Nova Vale on fame", "Her playlist featuring a new song")[0] == "other"
    assert stories.classify("Anything", source_key="pitchfork-reviews")[0] == "review"


def test_release_status_never_reads_a_tease_or_rumor_as_announced():
    assert stories.release_status("tease", "Nova Vale Teases New Album", None) == "teased"
    assert stories.release_status("album_announcement", "Nova Vale Reportedly Has a New Album Coming", None) == "rumored"
    assert stories.release_status("album_announcement", "Nova Vale Announces New Album", None) == "announced"
    assert stories.release_status("album_release", "Nova Vale's New Album Is Out Now", None) == "released"
    assert stories.release_status("tour", "Nova Vale Tour", None) is None


def test_release_title_and_date():
    assert stories.release_title("Nova Vale Announces New Album Glass Hours: Hear “Static”") == "Glass Hours"
    assert stories.release_title("Nova Vale Shares “Static”") == "Static"
    assert stories.release_title("Nova Vale Is Back") is None
    assert stories.release_date("The album is out March 6", date(2026, 10, 23)) == "2027-03-06"  # next March
    assert stories.release_date("arriving on Friday, November 6", date(2026, 10, 23)) == "2026-11-06"
    assert stories.release_date("due February 30", date(2026, 10, 23)) is None
    assert stories.release_date("out June 1, 2026", date(2026, 10, 23)) == "2026-06-01"  # explicit year wins


def test_one_announcement_from_three_feeds_is_one_story_with_three_sources(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours", "feed-a", 0, "Glass Hours is out March 6.")
    art(conn, "Nova Vale Details Glass Hours, Her New Album", "feed-b", 2)
    art(conn, "Nova Vale announces new album 'Glass Hours'", "feed-c", 5)
    [s] = build(conn)
    assert s["n"] == 3 and s["story_type"] == "album_announcement" and s["release_status"] == "announced"
    assert s["release_title"] == "Glass Hours" and s["release_date"] == "2027-03-06"
    assert s["verification"] == "corroborated" and s["sensitive"] == 0
    claims = {(r["claim"], r["value"]) for r in conn.execute("SELECT claim, value FROM story_evidence")}
    assert ("release_date", "2027-03-06") in claims and ("title", "Glass Hours") in claims


def test_a_later_different_date_marks_the_story_conflicting_without_overwriting(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours, Out March 6", "feed-a", 0)
    art(conn, "Nova Vale's Glass Hours Pushed to April 10", "feed-b", 30, "The new album Glass Hours is now due April 10.")
    stories_ = build(conn)
    s = next(x for x in stories_ if x["release_title"] == "Glass Hours")
    assert s["verification"] == "conflicting" and s["release_date"] == "2027-03-06"
    dates = {r[0] for r in conn.execute("SELECT value FROM story_evidence WHERE claim='release_date'")}
    assert dates == {"2027-03-06", "2027-04-10"}


def test_status_moves_forward_through_a_release_cycle(conn):
    art(conn, "Nova Vale Teases New Album Glass Hours", "feed-a", 0)
    art(conn, "Nova Vale Announces New Album Glass Hours", "feed-b", 48)
    art(conn, "Nova Vale's New Album Glass Hours Is Out Now", "feed-c", 24 * 14)  # inside the release window
    [s] = build(conn, T0 + timedelta(days=20))
    assert s["n"] == 3 and s["story_type"] == "album_release" and s["release_status"] == "released"


def test_different_things_about_one_artist_stay_separate(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours", "feed-a", 0)
    art(conn, "Nova Vale Is Now a Lego Set", "feed-a", 1)
    art(conn, "Nova Vale Cast in a New Film", "feed-b", 2)        # brand/media too, but a different thing
    art(conn, "Night Drive (Remix) – Song by Echo Bay featuring Nova Vale", "feed-b", 3)
    art(conn, "Nova Vale's Lego Set Sells Out", "feed-c", 4)      # same thing as the Lego story
    got = sorted((s["story_type"], s["n"]) for s in build(conn))
    assert got == [("album_announcement", 1), ("brand_collab", 1), ("brand_collab", 2), ("collaboration", 1)]


def test_window_and_primary_artist(conn):
    art(conn, "Nova Vale Announces 2027 Tour", "feed-a", 0)
    art(conn, "Nova Vale Adds Tour Dates", "feed-b", 24 * 5)          # past 72 hours: a new story
    art(conn, "Kilo Moon Joins Nova Vale on Tour", "feed-c", 1)        # Kilo Moon is named first
    s = build(conn)
    assert [(x["n"], x["primary_artist_id"]) for x in s] == [(1, 1), (1, 2), (1, 1)]
    assert s[1]["other_artist_ids"] == "[1]"


def test_sensitive_stories_are_flagged(conn):
    art(conn, "Iron Lark Arrested After Show", "feed-a", 0)
    [s] = build(conn)
    assert s["sensitive"] == 1 and s["story_type"] == "legal"


def test_only_headline_matches_make_stories(conn):
    art(conn, "Festival Lineup Revealed", "feed-a", 0, "With Nova Vale and others.")
    assert build(conn) == []


def test_rebuild_keeps_ids_and_your_feedback(conn):
    a1 = art(conn, "Nova Vale Announces 2027 Tour", "feed-a", 0)
    [s] = build(conn)
    conn.execute("UPDATE stories SET feedback='interested' WHERE id=?", (s["id"],))
    art(conn, "Nova Vale Announces 2027 Tour Dates", "feed-b", 3)
    [again] = build(conn)
    assert again["id"] == s["id"] and again["feedback"] == "interested" and again["n"] == 2
    assert again["key"] == f"1:tour:{a1}"
    # A story you acted on survives even when its articles leave the window
    stories.rebuild(conn, T0 + timedelta(days=60))
    assert conn.execute("SELECT feedback FROM stories").fetchall()[0][0] == "interested"
    # One you didn't is removed
    conn.execute("UPDATE stories SET feedback=NULL")
    stories.rebuild(conn, T0 + timedelta(days=60))
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0


def test_api_lists_stories_with_sources_and_evidence(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tracker import api
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    watchlist.add(c, "Nova Vale", "core")
    now = datetime.now(timezone.utc) - timedelta(hours=30)
    global T0
    old, T0 = T0, now
    try:
        art(c, "Nova Vale Announces New Album Glass Hours, Out March 6", "feed-a", 0)
        art(c, "Nova Vale Details Glass Hours, Her New Album", "feed-b", 1)
        watchlist.rematch(c)
    finally:
        T0 = old
    c.close()
    [s] = TestClient(api.app).get("/api/news/stories?days=7").json()
    assert s["primary_artist"]["name"] == "Nova Vale" and s["source_count"] == 2 and len(s["sources"]) == 2
    assert s["release_title"] == "Glass Hours" and s["verification"] == "corroborated" and s["sensitive"] is False
    assert {(e["claim"], e["provider"]) for e in s["evidence"]} >= {("title", "Feed-A"), ("release_date", "Feed-A")}


def test_stories_of_undated_first_read_items_are_marked(conn):
    a = art(conn, "Nova Vale Is Now a Lego Set", "feed-a", 0)
    conn.execute("UPDATE articles SET published_at=NULL, first_read=1 WHERE id=?", (a,))
    art(conn, "Kilo Moon Announces 2027 Tour", "feed-b", 0)
    got = {s["story_type"]: s["dated"] for s in build(conn)}
    assert got == {"brand_collab": 0, "tour": 1}
