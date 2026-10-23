"""Music watchlist M2: seeding from listening, edits that survive re-seeds, matching rules, ids, API."""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
import requests
from fastapi.testclient import TestClient

from tracker import api, config, db, listening, news, watchlist
from tracker.fetchers import musicbrainz
from tests.test_news import NOW, Resp, Session, SRC_A, SRC_B


def plays_file(path, plays: dict[str, int]):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS plays (artist TEXT, played_at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS artist_genres (artist_key TEXT PRIMARY KEY, primary_bucket TEXT)")
    con.execute("DELETE FROM plays")
    recent = (date.today() - timedelta(days=10)).isoformat()
    for name, n in plays.items():
        con.executemany("INSERT INTO plays VALUES (?,?)", [(name, recent)] * n)
    con.commit()
    con.close()


@pytest.fixture
def taste(tmp_path, monkeypatch):
    """Point LISTENING_DB at a fake plays.db; call the returned function to (re)write it and get a fresh Taste."""
    path = tmp_path / "plays.db"
    monkeypatch.setattr(config, "LISTENING_DB", str(path))

    def write(plays):
        plays_file(path, plays)
        listening._cache.clear()
        return listening.load()
    return write


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def article(conn, title, excerpt=None, source_key="feed-a"):
    sid = conn.execute("SELECT id FROM news_sources WHERE key=?", (source_key,)).fetchone()
    if sid is None:
        sid = [conn.execute("INSERT INTO news_sources (key, name, url) VALUES (?, ?, ?)",
                            (source_key, source_key, f"https://{source_key}.example/feed")).lastrowid]
    cur = conn.execute("""INSERT INTO articles (source_id, guid, url, title, excerpt, fetched_at)
                          VALUES (?, ?, ?, ?, ?, ?)""", (sid[0], title, f"https://x.example/{title}", title, excerpt,
                                                         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return cur.lastrowid


def links(conn):
    return sorted((r["title"], r["name"], r["location"], r["confidence"]) for r in conn.execute(
        """SELECT a.title, w.name, aa.location, aa.confidence FROM article_artists aa
           JOIN articles a ON a.id = aa.article_id JOIN artist_identities w ON w.id = aa.artist_id"""))


def artist(conn, name):
    return conn.execute("SELECT * FROM artist_identities WHERE name=?", (name,)).fetchone()


def test_seed_takes_the_rotation_and_keeps_the_most_played_spelling(conn, taste):
    t = taste({"Nova Vale": 40, "nova vale": 5, "Kilo Moon": 25, "Iron Lark": 24})
    assert watchlist.seed(conn, t) == {"added": 2, "returned": 0, "dropped": 0}
    assert [(r["name"], r["tier"], r["origin"]) for r in conn.execute("SELECT * FROM artist_identities ORDER BY name")] \
        == [("Kilo Moon", "core", "listening"), ("Nova Vale", "core", "listening")]
    # No play counts are stored
    assert "plays" not in {r[1] for r in conn.execute("PRAGMA table_info(artist_identities)")}


def test_reseed_drops_and_returns_artists_but_never_touches_your_edits(conn, taste):
    watchlist.seed(conn, taste({"Nova Vale": 40, "Kilo Moon": 30, "Iron Lark": 30}))
    watchlist.update(conn, artist(conn, "Iron Lark")["id"], {"muted": True})
    out = watchlist.seed(conn, taste({"Nova Vale": 40}))
    assert out == {"added": 0, "returned": 0, "dropped": 1}
    assert artist(conn, "Kilo Moon")["tier"] == "dropped"
    assert artist(conn, "Iron Lark")["tier"] == "core" and artist(conn, "Iron Lark")["muted"] == 1  # yours
    assert watchlist.seed(conn, taste({"Nova Vale": 40, "Kilo Moon": 30}))["returned"] == 1
    assert artist(conn, "Kilo Moon")["tier"] == "core"


def test_no_listening_history_changes_nothing(conn, taste, monkeypatch):
    watchlist.seed(conn, taste({"Nova Vale": 40}))
    monkeypatch.setattr(config, "LISTENING_DB", "")
    listening._cache.clear()
    assert watchlist.seed(conn) == {"added": 0, "returned": 0, "dropped": 0}
    assert artist(conn, "Nova Vale")["tier"] == "core"


def test_seed_links_the_events_pipelines_artist(conn, taste):
    conn.execute("INSERT INTO attractions (id, name, first_seen) VALUES ('K1', 'NOVA VALE', '2026-10-01')")
    watchlist.seed(conn, taste({"Nova Vale": 40}))
    assert artist(conn, "Nova Vale")["attraction_id"] == "K1"


def test_matching_title_excerpt_aliases_and_curly_quotes(conn):
    watchlist.add(conn, "Nova Vale", "core")
    watchlist.add(conn, "Kilo Moon", "core", aliases=["KLM"])
    watchlist.add(conn, "O'Malley Twins", "watch")
    article(conn, "Nova Vale Announces Tour")
    article(conn, "Festival lineup revealed", "Featuring Kilo Moon and more.")
    article(conn, "KLM Shares New Single")
    article(conn, "The O’Malley Twins Return")
    article(conn, "Nova Valentine is someone else")
    watchlist.rematch(conn)
    assert links(conn) == [
        ("Festival lineup revealed", "Kilo Moon", "excerpt", "medium"),
        ("KLM Shares New Single", "Kilo Moon", "title", "high"),
        ("Nova Vale Announces Tour", "Nova Vale", "title", "high"),
        ("The O’Malley Twins Return", "O'Malley Twins", "title", "high"),
    ]
    alias = conn.execute("SELECT match FROM article_artists aa JOIN articles a ON a.id=aa.article_id "
                         "WHERE a.title LIKE 'KLM%'").fetchone()
    assert alias[0] == "alias"


def test_common_word_names_need_a_music_word_and_never_match_on_the_summary(conn):
    article(conn, "What tomorrow holds for cities", "tomorrow is bright")  # corpus: "tomorrow" is a word
    watchlist.add(conn, "Tomorrow", "core")
    watchlist.add(conn, "YT", "core")  # two letters
    watchlist.refresh_common_words(conn)
    article(conn, "Tomorrow Drops Surprise Album")
    article(conn, "Tomorrow Plans For The City")
    article(conn, "Other news", "Tomorrow was there too")
    article(conn, "YT Shares Video")
    watchlist.rematch(conn)
    assert links(conn) == [("Tomorrow Drops Surprise Album", "Tomorrow", "title", "low"),
                           ("YT Shares Video", "YT", "title", "low")]
    # Your override wins over the automatic call
    watchlist.update(conn, artist(conn, "Tomorrow")["id"], {"common_word_override": False})
    assert ("Tomorrow Plans For The City", "Tomorrow", "title", "high") in links(conn)


def test_lowercase_styled_names_need_ordinary_use_in_several_other_articles(conn):
    about_it = [("softarcade shares new song", "softarcade is back"), ("Roundup", "with softarcade and friends")]
    assert watchlist.is_common_word("softarcade", about_it) is False  # one headline about it, one roundup
    word = [("Headline one", "into the void"), ("Headline two", "a void left"), ("Three", "the void again")]
    assert watchlist.is_common_word("void", word) is True
    assert watchlist.is_common_word("void", word[:2]) is False  # not enough evidence yet
    assert watchlist.is_common_word("Kilo Moon", [("x", "kilo moon in lowercase")]) is True
    assert watchlist.is_common_word("Kilo Moon", [("Kilo Moon in a headline", "")]) is False
    assert watchlist.is_common_word("YT", []) is True


def test_ambiguous_names_and_muted_or_dropped_artists(conn):
    a = watchlist.add(conn, "Iron Lark", "core")
    b = watchlist.add(conn, "Soft Arcade", "core")
    c = watchlist.add(conn, "Echo Bay", "core")
    conn.execute("UPDATE artist_identities SET ambiguous_name=1 WHERE id=?", (a,))
    article(conn, "Roundup", "Iron Lark, Soft Arcade and Echo Bay all played.")
    article(conn, "Iron Lark Announces Album")
    watchlist.update(conn, b, {"muted": True})
    watchlist.update(conn, c, {"tier": "dropped"})
    assert links(conn) == [("Iron Lark Announces Album", "Iron Lark", "title", "high")]


def test_longest_name_wins(conn):
    watchlist.add(conn, "Lil Vale", "core")
    watchlist.add(conn, "Vale", "core")
    article(conn, "Lil Vale Shares Song")
    watchlist.rematch(conn)
    assert links(conn) == [("Lil Vale Shares Song", "Lil Vale", "title", "high")]


def test_bad_edits_are_refused(conn):
    a = watchlist.add(conn, "Nova Vale")
    for fields in ({"tier": "superstar"}, {"aliases": "not a list"}, {"common_word_override": "maybe"}):
        with pytest.raises(watchlist.WatchlistError):
            watchlist.update(conn, a, fields)
    with pytest.raises(watchlist.WatchlistError):
        watchlist.add(conn, "   ")
    with pytest.raises(KeyError):
        watchlist.update(conn, 999, {"muted": True})


def test_news_run_tags_new_articles_and_builds_the_watchlist_the_first_time(conn, taste, monkeypatch):
    monkeypatch.setattr(config, "NEWS_SOURCES", [SRC_A, SRC_B])
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    from tests.test_news import RSS, ATOM
    taste({"Nova Vale": 40, "Iron Lark": 30})
    news.run(conn, Session({SRC_A["url"]: [Resp(200, RSS)], SRC_B["url"]: [Resp(200, ATOM)]}), NOW)
    assert {(t, n) for t, n, *_ in links(conn)} == {("Nova Vale Announces New Album Glass Hours", "Nova Vale"),
                                                    ("Iron Lark Tour Dates", "Iron Lark")}


class FakeDeezer:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on

    def find_artist(self, name):
        if name == self.fail_on:
            raise requests.RequestException("Deezer quota")
        return {"id": len(name), "name": name}


class FakeMB:
    def find_artist(self, name):
        return ("mbid-" + name, False) if name != "Echo Bay" else (None, True)


def test_resolve_ids_and_stop_on_a_rate_limit(conn):
    for n in ("Nova Vale", "Echo Bay", "Kilo Moon"):
        watchlist.add(conn, n, "core")
    article(conn, "Roundup", "Echo Bay played.")
    watchlist.rematch(conn)
    assert links(conn) == [("Roundup", "Echo Bay", "excerpt", "medium")]
    assert watchlist.resolve_ids(conn, 10, FakeDeezer(fail_on="Kilo Moon"), FakeMB()) == 2
    nv, eb, km = artist(conn, "Nova Vale"), artist(conn, "Echo Bay"), artist(conn, "Kilo Moon")
    assert (nv["deezer_id"], nv["mbid"], nv["ambiguous_name"]) == (9, "mbid-Nova Vale", 0)
    assert eb["mbid"] is None and eb["ambiguous_name"] == 1 and km["ids_checked_at"] is None
    assert links(conn) == []  # an ambiguous name loses its summary-only match
    assert watchlist.resolve_ids(conn, 10, FakeDeezer(), FakeMB()) == 1  # only Kilo Moon was left


class MBSession:
    def __init__(self, artists, status=200):
        self.artists, self.status = artists, status
        self.params = None

    def get(self, url, params=None, headers=None, timeout=None):
        self.params, self.headers = params, headers
        body = {"artists": self.artists}
        r = requests.Response()
        r.status_code, r._content = self.status, json.dumps(body).encode()
        return r


def test_musicbrainz_takes_only_one_strong_exact_match():
    one = MBSession([{"id": "a1", "name": "Nova Vale", "score": 100}, {"id": "a2", "name": "Nova Valery", "score": 95}])
    client = musicbrainz.MusicBrainzClient(one, min_interval=0)
    assert client.find_artist("nova vale") == ("a1", False)
    assert one.params["query"] == 'artist:"nova vale"' and "github.com" in one.headers["User-Agent"]
    two = MBSession([{"id": "a1", "name": "Tomorrow", "score": 100}, {"id": "a2", "name": "Tomorrow", "score": 100}])
    assert musicbrainz.MusicBrainzClient(two, min_interval=0).find_artist("Tomorrow") == (None, True)
    alias = MBSession([{"id": "a3", "name": "Kilo Moon Band", "score": 92, "aliases": [{"name": "Kilo Moon"}]}])
    assert musicbrainz.MusicBrainzClient(alias, min_interval=0).find_artist("Kilo Moon") == ("a3", False)
    weak = MBSession([{"id": "a4", "name": "Echo Bay", "score": 60}])
    assert musicbrainz.MusicBrainzClient(weak, min_interval=0).find_artist("Echo Bay") == (None, False)
    with pytest.raises(requests.RequestException):
        musicbrainz.MusicBrainzClient(MBSession([], 503), min_interval=0).find_artist("Echo Bay")


def test_api_watchlist_and_news_tags(tmp_path, monkeypatch, taste):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    taste({"Nova Vale": 40})
    c = db.connect(path)
    watchlist.seed(c)
    article(c, "Nova Vale Announces Tour")
    article(c, "Unrelated Headline")
    watchlist.rematch(c)
    c.close()
    client = TestClient(api.app)

    wl = client.get("/api/watchlist").json()
    assert [(w["name"], w["tier"], w["plays"], w["articles_30d"]) for w in wl] == [("Nova Vale", "core", 40, 1)]
    news_all = client.get("/api/news?days=1").json()
    assert len(news_all) == 2
    mine = client.get("/api/news?days=1&mine=true").json()
    assert [a["title"] for a in mine] == ["Nova Vale Announces Tour"]
    assert mine[0]["artists"] == [{"id": wl[0]["id"], "name": "Nova Vale", "tier": "core", "match": "exact",
                                   "location": "title", "confidence": "high", "plays": 40}]

    r = client.post("/api/watchlist", json={"name": "Unrelated", "tier": "watch"})
    assert r.status_code == 201
    assert len(client.get("/api/news?days=1&mine=true").json()) == 2
    assert client.post("/api/watchlist", json={"name": "X", "tier": "superstar"}).status_code == 400
    r = client.patch(f"/api/watchlist/{wl[0]['id']}", json={"muted": True})
    assert r.status_code == 200 and r.json()["muted"] == 1
    assert [a["title"] for a in client.get("/api/news?days=1&mine=true").json()] == ["Unrelated Headline"]
    assert client.patch(f"/api/watchlist/{wl[0]['id']}", json={"plays": 9}).status_code == 400
    assert client.patch("/api/watchlist/999", json={"muted": True}).status_code == 404
