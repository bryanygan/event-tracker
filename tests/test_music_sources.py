"""Music news M8: MusicBrainz links, YouTube (Data API) channels as sources, official uploads confirming stories, the
quota guard, and the cross-pillar link (event ideas show their acts' recent music stories)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi.testclient import TestClient

from tracker import api, config, db, music, news, stories, watchlist
from tracker.fetchers import musicbrainz, youtube
from tests.test_news import Resp, Session

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)
OWN = "UC" + "a" * 22      # Nova Vale's own channel
OUTLET = "UC" + "b" * 22   # a performance-video outlet
FEED = {"key": "feed-a", "name": "Feed A", "url": "https://a.example/feed", "niches": ["pop"]}
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>A</title>
<item><title>Nova Vale Shares New Song \xe2\x80\x9cGlass Hours\xe2\x80\x9d</title><link>https://a.example/nv</link>
  <guid>nv-1</guid><pubDate>Fri, 23 Oct 2026 15:00:00 +0000</pubDate></item></channel></rss>"""


def upload(video, title, hours_ago, description=""):
    when = (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"snippet": {"title": title, "description": description, "publishedAt": when,
                        "resourceId": {"videoId": video}},
            "contentDetails": {"videoId": video, "videoPublishedAt": when}}


class FakeAPI:
    """The two Data API calls we make: channels.list (by id or handle) and playlistItems.list."""
    def __init__(self, uploads, error=None):
        self.uploads, self.error = uploads, error
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url.rsplit("/", 1)[-1], dict(params)))
        r = requests.Response()
        if self.error:
            r.status_code, r._content = 403, json.dumps({"error": {"code": 403, "errors": [{"reason": self.error}]}}).encode()
            return r
        if url.endswith("/channels"):
            cid = params.get("id") or {"@novavale": OWN}.get(params.get("forHandle"))
            body = {"items": [{"id": cid, "snippet": {"title": cid},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}}}]} if cid else {}
        else:
            body = {"items": self.uploads.get(params["playlistId"], [])}
        r.status_code, r._content = 200, json.dumps(body).encode()
        return r


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(config, "NEWS_SOURCES", [FEED])
    monkeypatch.setattr(config, "YOUTUBE_SOURCES", [
        {"key": "yt-outlet", "name": "Outlet (YouTube)", "channel_id": OUTLET, "niches": ["rnb"]}])
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    c = db.connect(":memory:")
    for name in ("Nova Vale", "Kilo Moon"):
        watchlist.add(c, name, "core")
    c.execute("UPDATE artist_identities SET links=? WHERE name='Nova Vale'",
              (json.dumps([{"type": "youtube", "url": f"https://www.youtube.com/channel/{OWN}"},
                           {"type": "homepage", "url": "https://novavale.example"}]),))
    c.commit()
    yield c
    c.close()


UPLOADS = {
    "UU" + OWN[2:]: [upload("v1", "Glass Hours (Official Video)", 2), upload("v2", "studio day #shorts", 1),
                     upload("v3", "Private video", 1), upload("v0", "Old Song (Official Audio)", 24 * 40)],
    "UU" + OUTLET[2:]: [upload("v4", "Kilo Moon - Static | A COLORS SHOW", 3)],
}


def test_musicbrainz_links_keep_the_artists_own_pages_and_skip_ended_ones():
    class S:
        def get(self, url, params=None, headers=None, timeout=None):
            self.url, self.params = url, params
            r = requests.Response()
            r.status_code, r._content = 200, json.dumps({"relations": [
                {"type": "youtube", "url": {"resource": f"https://www.youtube.com/channel/{OWN}"}},
                {"type": "official homepage", "url": {"resource": "https://novavale.example"}},
                {"type": "youtube", "ended": True, "url": {"resource": "https://www.youtube.com/user/oldname"}},
                {"type": "streaming", "url": {"resource": "https://stream.example/nv"}}]}).encode()
            return r
    s = S()
    out = musicbrainz.MusicBrainzClient(s, min_interval=0).links("mbid-1")
    assert out == [{"type": "youtube", "url": f"https://www.youtube.com/channel/{OWN}"},
                   {"type": "homepage", "url": "https://novavale.example"}]
    assert s.url.endswith("/artist/mbid-1") and s.params["inc"] == "url-rels"


def test_channel_links_the_api_can_resolve():
    assert youtube.channel_ref(f"https://www.youtube.com/channel/{OWN}") == {"id": OWN}
    assert youtube.channel_ref("https://youtube.com/@novavale") == {"handle": "@novavale"}
    assert youtube.channel_ref("https://www.youtube.com/user/novavalemusic") == {"user": "novavalemusic"}
    assert youtube.channel_ref("https://www.youtube.com/c/NovaVale") is None  # custom URLs: no API lookup
    assert youtube.channel_ref("https://www.youtube.com/watch?v=x") is None


def test_uploads_become_articles_without_shorts_or_private_videos():
    client = youtube.YouTubeClient("k", FakeAPI(UPLOADS))
    got = client.uploads("UU" + OWN[2:])
    assert [a["title"] for a in got] == ["Glass Hours (Official Video)", "Old Song (Official Audio)"]
    assert got[0]["guid"] == "yt:v1" and got[0]["url"] == "https://www.youtube.com/watch?v=v1"
    assert got[0]["published_at"] == "2026-10-23T16:00:00+00:00" and client.units == 1


def test_api_errors_never_show_the_key_and_quota_is_its_own_error():
    with pytest.raises(youtube.QuotaExceeded):
        youtube.YouTubeClient("secret-key", FakeAPI({}, error="quotaExceeded")).uploads("UU1")
    with pytest.raises(news.news_rss.FeedError) as e:
        youtube.YouTubeClient("secret-key", FakeAPI({}, error="forbidden")).uploads("UU1")
    assert "secret-key" not in str(e.value) and "403" in str(e.value)


@pytest.mark.parametrize("title, song", [
    ("Glass Hours (Official Video)", "Glass Hours"),
    ("Nova Vale - Glass Hours (Official Music Video)", "Glass Hours"),
    ("Nova Vale - Glass Hours | A COLORS SHOW", "Glass Hours"),
    ("Nova Vale – Glass Hours ft. Kilo Moon (Directed by Someone) [4K]", "Glass Hours"),
    ("Echo Bay - Glass Hours (Official Audio)", "Echo Bay - Glass Hours"),  # not a name we know: left as is
])
def test_youtube_titles(title, song):
    assert stories.youtube_title(title, ["Nova Vale"]) == song


def test_an_official_video_is_a_released_song_even_with_a_scary_word():
    assert stories.classify("Killed It (Official Video)", kind="youtube")[0] == "single_release"
    assert stories.classify("Killed It (Official Video)")[0] == "sensitive"  # the same words in a headline
    assert stories.release_status("single_release", "Glass Hours (Official Video)", None, uploaded=True) == "released"
    assert stories.classify("Nova Vale Tour Trailer", kind="youtube")[0] == "tour"  # other uploads: the usual rules


def test_without_a_key_youtube_sources_wait_and_cost_nothing(conn):
    fake = FakeAPI(UPLOADS)
    s = Session({FEED["url"]: [Resp(200, RSS)]})
    out = news.run(conn, s, NOW, youtube_client=youtube.YouTubeClient("", fake))
    assert out["read"] == 1 and fake.calls == []
    rows = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM news_sources")}
    assert rows["yt-outlet"]["kind"] == "youtube" and rows["yt-outlet"]["origin"] == "config"
    own = rows[f"yt-artist:{OWN}"]
    assert own["origin"] == "musicbrainz" and own["name"] == "Nova Vale (YouTube)" and own["enabled"] == 1
    assert own["poll_minutes"] == config.YOUTUBE_ARTIST_POLL_MINUTES


def test_an_upload_on_the_artists_own_channel_confirms_the_story(conn, monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "k")
    fake = FakeAPI(UPLOADS)
    out = news.run(conn, Session({FEED["url"]: [Resp(200, RSS)]}), NOW, youtube_client=youtube.YouTubeClient("k", fake))
    assert out["read"] == 3 and not out["failed"]
    # One channel lookup + one uploads read per channel, all counted against our daily cap
    assert sorted(c[0] for c in fake.calls) == ["channels", "channels", "playlistItems", "playlistItems"]
    assert news.youtube_units(conn, NOW) == 4
    titles = {r["title"] for r in conn.execute("SELECT title FROM articles")}
    assert "Glass Hours (Official Video)" in titles and "studio day #shorts" not in titles
    assert "Old Song (Official Audio)" not in titles  # older than NEWS_MAX_AGE_DAYS
    tag = conn.execute("""SELECT aa.match, aa.confidence FROM article_artists aa JOIN articles a ON a.id = aa.article_id
                          WHERE a.title = 'Glass Hours (Official Video)'""").fetchone()
    assert tuple(tag) == ("owner", "high")  # the title never names her: her channel does
    story = conn.execute("SELECT * FROM stories WHERE release_title = 'Glass Hours'").fetchone()
    assert story["story_type"] == "single_release" and story["release_status"] == "released"
    assert story["verification"] == "confirmed"  # the outlet's article and her own upload: one story
    kinds = {e["kind"] for e in conn.execute("SELECT kind FROM story_evidence WHERE story_id=?", (story["id"],))}
    assert kinds == {"article", "official"}
    colors = conn.execute("SELECT * FROM stories WHERE release_title = 'Static'").fetchone()
    assert colors["verification"] == "unverified" and colors["release_status"] == "released"  # an outlet, not hers
    # The second check: the channel ids are cached, and nothing is due again for an hour (outlets) or 6 (artists)
    fake.calls.clear()
    news.run(conn, Session({FEED["url"]: [Resp(304)]}), NOW + timedelta(minutes=30), youtube_client=youtube.YouTubeClient("k", fake))
    assert fake.calls == []
    news.run(conn, Session({FEED["url"]: [Resp(304)]}), NOW + timedelta(minutes=62), youtube_client=youtube.YouTubeClient("k", fake))
    assert [c[0] for c in fake.calls] == ["playlistItems"]  # the outlet again; its uploads playlist was cached


def test_the_daily_cap_and_a_used_up_quota_stop_youtube_reads(conn, monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "k")
    monkeypatch.setattr(config, "YOUTUBE_DAILY_UNITS", 3)  # room for one channel (lookup + uploads), not two
    fake = FakeAPI(UPLOADS)
    news.run(conn, Session({FEED["url"]: [Resp(200, RSS)]}), NOW, youtube_client=youtube.YouTubeClient("k", fake))
    assert len(fake.calls) == 2 and news.youtube_units(conn, NOW) == 2
    monkeypatch.setattr(config, "YOUTUBE_DAILY_UNITS", 100)
    quota = FakeAPI({}, error="quotaExceeded")
    news.run(conn, Session({FEED["url"]: [Resp(304)]}), NOW + timedelta(minutes=5),
             youtube_client=youtube.YouTubeClient("k", quota))
    assert len(quota.calls) == 1  # the first refusal stops the rest
    failed = conn.execute("SELECT failures, last_error FROM news_sources WHERE last_error LIKE '%quota%'").fetchone()
    assert failed["failures"] == 0  # not the channel's fault: no back-off


def test_a_muted_artists_channel_is_disabled(conn):
    news.sync_artist_channels(conn)
    conn.execute("UPDATE artist_identities SET muted=1 WHERE name='Nova Vale'")
    assert news.sync_artist_channels(conn) == 0
    assert conn.execute("SELECT enabled FROM news_sources WHERE key=?", (f"yt-artist:{OWN}",)).fetchone()[0] == 0


def test_resolve_ids_reads_links_for_artists_whose_ids_are_already_fresh(conn):
    class MB:
        def find_artist(self, name):
            raise AssertionError("ids are fresh: no search")

        def links(self, mbid):
            return [{"type": "youtube", "url": "https://www.youtube.com/@novavale"}]
    conn.execute("UPDATE artist_identities SET mbid='m1', ids_checked_at=?, links=NULL WHERE name='Nova Vale'",
                 (datetime.now(timezone.utc).isoformat(),))
    conn.execute("UPDATE artist_identities SET ids_checked_at=? WHERE name='Kilo Moon'", (datetime.now(timezone.utc).isoformat(),))
    assert watchlist.resolve_ids(conn, 10, None, MB()) == 1
    row = conn.execute("SELECT links, links_checked_at FROM artist_identities WHERE name='Nova Vale'").fetchone()
    assert json.loads(row["links"])[0]["url"].endswith("@novavale") and row["links_checked_at"]


# ---------- cross-pillar ----------

def test_event_ideas_show_their_acts_recent_music_stories(tmp_path, monkeypatch):
    path = tmp_path / "x.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    for name in ("Nova Vale", "Kilo Moon", "Iron Lark"):
        watchlist.add(c, name, "core")
    sid = c.execute("INSERT INTO news_sources (key, name, url) VALUES ('feed-a', 'Feed A', 'https://a.example')").lastrowid
    now = datetime.now(timezone.utc)
    for i, (title, hours) in enumerate([("Nova Vale Announces New Album Glass Hours", 5),
                                        ("Kilo Moon Shares New Song “Static”", 2),
                                        ("Iron Lark Weighs In on the Election", 1)]):
        when = (now - timedelta(hours=hours)).isoformat()
        c.execute("""INSERT INTO articles (source_id, guid, url, title, published_at, fetched_at, title_hash)
                     VALUES (?,?,?,?,?,?,?)""", (sid, f"g{i}", f"https://a.example/{i}", title, when, when, f"h{i}"))
    watchlist.rematch(c)
    weekend = {"format": "weekend", "shows": [{"artist": "Nova Vale", "support": "Kilo Moon, Someone Else"},
                                              {"artist": "Iron Lark"}]}
    for key, kind, facts in [("w", "weekend", weekend), ("m", "music_story", {"format": "music_story", "artist": "Nova Vale"})]:
        c.execute("INSERT INTO alerts (key, kind, title, score, facts, created_at) VALUES (?,?,?,?,?,?)",
                  (key, kind, key, 50, json.dumps(facts), now.isoformat()))
    c.commit()
    index = music.news_index(c)
    found = music.news_for_event(index, weekend)
    assert [s["artist"] for s in found] == ["Kilo Moon", "Nova Vale"]  # newest first; support acts count; no politics
    assert found[0]["url"] == "https://a.example/1" and found[1]["type_label"]
    c.close()
    by_kind = {a["kind"]: a for a in TestClient(api.app).get("/api/alerts").json()}
    assert [s["artist"] for s in by_kind["weekend"]["music_news"]] == ["Kilo Moon", "Nova Vale"]
    assert by_kind["music_story"]["music_news"] == []  # a music idea is the story itself
    assert "music_news" not in by_kind["weekend"]["facts"]  # never facts: a draft can't repeat a headline


def test_sources_api_reports_youtube_status_without_the_key(tmp_path, monkeypatch, conn):
    path = tmp_path / "s.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    watchlist.add(c, "Nova Vale", "core")
    c.execute("UPDATE artist_identities SET links=? WHERE name='Nova Vale'",
              (json.dumps([{"type": "youtube", "url": f"https://www.youtube.com/channel/{OWN}"}]),))
    news.seed_sources(c)
    news.sync_artist_channels(c)
    c.close()
    body = TestClient(api.app).get("/api/news/sources").json()
    assert body["youtube"] == {"enabled": False, "units_today": 0, "daily_cap": config.YOUTUBE_DAILY_UNITS}
    own = next(s for s in body["sources"] if s["official"])
    assert own["needs_key"] is True and own["kind"] == "youtube" and own["origin"] == "musicbrainz"
    assert next(s for s in body["sources"] if s["key"] == "feed-a")["needs_key"] is False
