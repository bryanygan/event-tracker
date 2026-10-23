"""Music news M1: conditional requests, RSS + Atom parsing, dedupe, dates, failures keep articles, the lock."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, config, db, news
from tracker.fetchers import news_rss

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Feed</title>
<item><title>Nova Vale Announces New Album &lt;i&gt;Glass Hours&lt;/i&gt;</title>
  <link>https://example.com/nova-vale-album</link><guid isPermaLink="false">nv-1</guid>
  <pubDate>Fri, 23 Oct 2026 13:30:00 -0400</pubDate>
  <description><![CDATA[<p>Nova Vale&#8217;s third album arrives in March.</p>]]></description>
  <category>News</category><category>Pop</category></item>
<item><title>Kilo Moon Shares &#8220;Static&#8221;</title><link>https://example.com/kilo-moon-static</link>
  <pubDate></pubDate></item>
<item><title>Old story</title><link>https://example.com/old</link><guid>old-1</guid>
  <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate></item>
<item><link>https://example.com/no-title</link><guid>nt</guid></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom feed</title>
<entry><title type="html">Iron Lark Tour Dates</title><id>tag:example.com,2026:il-1</id>
  <link rel="alternate" href="https://example.com/iron-lark-tour"/><link rel="enclosure" href="https://example.com/x.jpg"/>
  <updated>2026-10-23T12:00:00Z</updated><published>2026-10-22T18:00:00</published>
  <summary>Soft Arcade opens.</summary><category term="Tours"/></entry>
</feed>"""


class Resp:
    def __init__(self, status=200, body=b"", headers=None, exc=None):
        self.status_code, self.body, self.headers, self.exc = status, body, headers or {}, exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class Session:
    """Answers each URL from a queue of Resp objects and records the request headers."""
    def __init__(self, answers):
        self.answers = {url: list(rs) for url, rs in answers.items()}
        self.calls = []

    def get(self, url, headers=None, **kw):
        self.calls.append((url, headers or {}))
        r = self.answers[url].pop(0)
        if r.exc:
            raise r.exc
        return r


SRC_A = {"key": "feed-a", "name": "Feed A", "url": "https://a.example/feed", "niches": ["pop"]}
SRC_B = {"key": "feed-b", "name": "Feed B", "url": "https://b.example/atom", "niches": ["rap"]}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(config, "NEWS_SOURCES", [SRC_A, SRC_B])
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    c = db.connect(":memory:")
    yield c
    c.close()


def titles(conn):
    return [r["title"] for r in conn.execute("SELECT title FROM articles ORDER BY id")]


def source(conn, key):
    return conn.execute("SELECT * FROM news_sources WHERE key=?", (key,)).fetchone()


def test_rss_parsing_cleans_text_and_converts_dates_to_utc():
    items = news_rss.parse(RSS)
    assert [a["title"] for a in items] == ["Nova Vale Announces New Album Glass Hours", "Kilo Moon Shares “Static”",
                                           "Old story"]  # the untitled item is dropped
    first = items[0]
    assert first["guid"] == "nv-1" and first["url"] == "https://example.com/nova-vale-album"
    assert first["published_at"] == "2026-10-23T17:30:00+00:00"
    assert first["excerpt"] == "Nova Vale’s third album arrives in March." and first["categories"] == ["News", "Pop"]
    assert items[1]["guid"] == "https://example.com/kilo-moon-static"  # no <guid>: the link stands in
    assert items[1]["published_at"] is None  # empty <pubDate>


def test_atom_prefers_published_and_the_alternate_link_and_reads_timezone_less_dates_as_utc():
    [a] = news_rss.parse(ATOM)
    assert a["url"] == "https://example.com/iron-lark-tour" and a["guid"] == "tag:example.com,2026:il-1"
    assert a["published_at"] == "2026-10-22T18:00:00+00:00" and a["categories"] == ["Tours"]


def test_broken_xml_falls_back_to_item_blocks_and_junk_is_an_error():
    broken = b"<rss><channel><item><title><![CDATA[Echo Bay & Friends]]></title><link>https://example.com/eb</link>" \
             b"<pubDate>Fri, 23 Oct 2026 10:00:00 +0000</pubDate></item> &nbsp; </channel></rss>"
    [a] = news_rss.parse(broken)
    assert a["title"] == "Echo Bay & Friends" and a["published_at"] == "2026-10-23T10:00:00+00:00"
    with pytest.raises(news_rss.FeedError):
        news_rss.parse(b"<html><body>Down for maintenance</body></html>")
    with pytest.raises(news_rss.FeedError):
        news_rss.parse(b"not xml at all")


def test_title_hash_ignores_case_punctuation_and_quote_style():
    assert news_rss.title_hash("Kilo Moon Shares “Static”") == news_rss.title_hash('kilo moon shares "static"!')
    assert news_rss.title_hash("Kilo Moon Shares Static") != news_rss.title_hash("Kilo Moon Shares Noise")


def test_first_read_stores_recent_items_and_marks_them_as_already_in_the_feed(conn):
    s = Session({SRC_A["url"]: [Resp(200, RSS, {"ETag": '"v1"', "Last-Modified": "Fri, 23 Oct 2026 14:00:00 GMT"})],
                 SRC_B["url"]: [Resp(200, ATOM)]})
    out = news.run(conn, s, NOW)
    assert out["new"] == 3 and out["read"] == 2 and out["failed"] == []
    assert titles(conn) == ["Nova Vale Announces New Album Glass Hours", "Kilo Moon Shares “Static”",
                            "Iron Lark Tour Dates"]  # "Old story" is older than NEWS_MAX_AGE_DAYS
    assert all(r[0] == 1 for r in conn.execute("SELECT first_read FROM articles"))
    a = source(conn, "feed-a")
    assert a["etag"] == '"v1"' and a["last_ok_at"] == NOW.isoformat() and a["failures"] == 0
    run = conn.execute("SELECT * FROM runs WHERE source='news'").fetchone()
    assert run["ok"] == 1 and run["events_fetched"] == 3 and run["api_calls"] == 2


def test_not_modified_sends_validators_and_costs_nothing(conn):
    s = Session({SRC_A["url"]: [Resp(200, RSS, {"ETag": '"v1"', "Last-Modified": "Fri, 23 Oct 2026 14:00:00 GMT"}),
                                Resp(304)],
                 SRC_B["url"]: [Resp(200, ATOM), Resp(304)]})
    news.run(conn, s, NOW)
    out = news.run(conn, s, NOW + timedelta(minutes=30))
    assert out["new"] == 0 and out["not_modified"] == 2
    url, headers = s.calls[2]
    assert url == SRC_A["url"] and headers["If-None-Match"] == '"v1"'
    assert headers["If-Modified-Since"] == "Fri, 23 Oct 2026 14:00:00 GMT"
    a = source(conn, "feed-a")
    assert a["etag"] == '"v1"' and a["last_not_modified_at"] == (NOW + timedelta(minutes=30)).isoformat()


def test_later_items_are_new_and_duplicates_by_guid_or_url_are_skipped(conn):
    later = RSS.replace(b"<channel><title>Feed</title>", b"""<channel><title>Feed</title>
        <item><title>Soft Arcade Signs To A Label</title><link>https://example.com/soft-arcade</link><guid>sa-1</guid>
        <pubDate>Fri, 23 Oct 2026 18:10:00 +0000</pubDate></item>""")
    # The same Kilo Moon story again, now with a guid (sites do change these): known by its URL
    later = later.replace(b"<link>https://example.com/kilo-moon-static</link>",
                          b"<link>https://example.com/kilo-moon-static</link><guid>km-1</guid>")
    s = Session({SRC_A["url"]: [Resp(200, RSS), Resp(200, later)], SRC_B["url"]: [Resp(200, ATOM), Resp(304)]})
    news.run(conn, s, NOW)
    out = news.run(conn, s, NOW + timedelta(minutes=30))
    assert out["new"] == 1 and titles(conn)[-1] == "Soft Arcade Signs To A Label"
    assert conn.execute("SELECT first_read FROM articles WHERE guid='sa-1'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 4


def test_a_broken_feed_keeps_its_articles_and_validators_and_backs_off(conn):
    ok = {"ETag": '"v1"'}
    s = Session({SRC_A["url"]: [Resp(200, RSS, ok), Resp(500), Resp(200, b"<html>oops</html>"),
                                Resp(exc=news_rss.requests.ConnectionError())],
                 SRC_B["url"]: [Resp(200, ATOM)] + [Resp(304)] * 4})
    news.run(conn, s, NOW)
    t = NOW + timedelta(minutes=30)
    out = news.run(conn, s, t)
    assert out["failed"] == ["feed-a (HTTP 500)"]
    a = source(conn, "feed-a")
    assert a["failures"] == 1 and a["last_error"] == "HTTP 500" and a["etag"] == '"v1"'
    assert len(titles(conn)) == 3  # nothing erased
    assert conn.execute("SELECT ok, error FROM runs ORDER BY id DESC").fetchone()[:] == (1, "failed: feed-a (HTTP 500)")

    # After 1 failure: due again in 30 minutes; after 2: 60
    t += timedelta(minutes=30)
    news.run(conn, s, t)
    assert source(conn, "feed-a")["failures"] == 2 and source(conn, "feed-a")["last_error"].startswith("not a feed")
    t += timedelta(minutes=30)
    assert news.run(conn, s, t)["read"] == 1  # feed-a waits
    t += timedelta(minutes=30)
    out = news.run(conn, s, t)
    assert out["failed"] == ["feed-a (ConnectionError)"] and source(conn, "feed-a")["failures"] == 3
    assert news.wait_minutes(source(conn, "feed-a")) == 120
    assert len(titles(conn)) == 3


def test_recovery_resets_the_backoff(conn):
    s = Session({SRC_A["url"]: [Resp(500), Resp(304)], SRC_B["url"]: [Resp(200, ATOM), Resp(304)]})
    news.run(conn, s, NOW)
    news.run(conn, s, NOW + timedelta(minutes=30))
    a = source(conn, "feed-a")
    assert a["failures"] == 0 and a["last_error"] is None and news.wait_minutes(a) == 30


def test_oversized_feed_is_refused(conn, monkeypatch):
    monkeypatch.setattr(config, "NEWS_MAX_BYTES", 1000)
    s = Session({SRC_A["url"]: [Resp(200, RSS * 3)], SRC_B["url"]: [Resp(200, ATOM)]})
    out = news.run(conn, s, NOW)
    assert out["failed"] == ["feed-a (feed larger than 0 MB)"] and len(titles(conn)) == 1


def test_sources_removed_from_config_are_disabled_not_deleted(conn, monkeypatch):
    s = Session({SRC_A["url"]: [Resp(200, RSS)], SRC_B["url"]: [Resp(200, ATOM)]})
    news.run(conn, s, NOW)
    monkeypatch.setattr(config, "NEWS_SOURCES", [SRC_B])
    news.seed_sources(conn)
    assert source(conn, "feed-a")["enabled"] == 0 and len(titles(conn)) == 3
    monkeypatch.setattr(config, "NEWS_SOURCES", [])
    news.seed_sources(conn)
    assert source(conn, "feed-b")["enabled"] == 0


def test_the_job_skips_politely_while_a_pipeline_run_holds_the_lock(monkeypatch, tmp_path, caplog):
    from tracker import run as pipeline
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(pipeline, "setup_logging", lambda: None)
    called = []
    monkeypatch.setattr(news, "run", lambda conn: called.append(1))
    with pipeline.single_run(), caplog.at_level("INFO", logger="tracker.news"):
        news.main()
    assert called == [] and "Skipped this news check" in caplog.text
    news.main()
    assert called == [1]


def test_api_lists_articles_and_source_health_and_keeps_news_out_of_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "NEWS_SOURCES", [SRC_A, SRC_B])
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    c.execute("INSERT INTO runs (started_at, finished_at, source, ok) VALUES (?, ?, 'ticketmaster', 1)",
              (NOW.isoformat(), NOW.isoformat()))
    news.run(c, Session({SRC_A["url"]: [Resp(200, RSS)], SRC_B["url"]: [Resp(500)]}), NOW)
    c.close()
    client = TestClient(api.app)

    arts = client.get("/api/news?days=100000").json()
    assert [a["title"] for a in arts] == ["Nova Vale Announces New Album Glass Hours", "Kilo Moon Shares “Static”"]
    assert arts[0]["source_name"] == "Feed A" and arts[0]["categories"] == ["News", "Pop"] and arts[0]["first_read"]
    assert arts[1]["published_at"] is None and arts[1]["seen_at"] == NOW.isoformat()
    assert client.get("/api/news?days=100000&source=feed-b").json() == []

    health = client.get("/api/news/sources").json()
    by_key = {h["key"]: h for h in health["sources"]}
    assert by_key["feed-b"]["last_error"] == "HTTP 500" and by_key["feed-b"]["failures"] == 1
    assert by_key["feed-a"]["articles"] == 2 and by_key["feed-a"]["niches"] == ["pop"]
    assert [r["source"] for r in health["checks"]] == ["news"]
    assert [r["source"] for r in client.get("/api/runs").json()] == ["ticketmaster"]
    assert client.get("/api/summary").json()["last_run"]["source"] == "ticketmaster"


def test_a_future_feed_date_is_capped_at_when_we_saw_it(conn):
    ahead = RSS.replace(b"Fri, 23 Oct 2026 13:30:00 -0400", b"Fri, 23 Oct 2026 19:00:00 +0000")  # NOW is 18:00 UTC
    news.run(conn, Session({SRC_A["url"]: [Resp(200, ahead)], SRC_B["url"]: [Resp(304)]}), NOW)
    assert conn.execute("SELECT published_at FROM articles WHERE guid='nv-1'").fetchone()[0] == NOW.isoformat()
