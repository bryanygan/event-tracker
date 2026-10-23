"""Music news M4: release catalogs (Deezer + iTunes), catalog evidence on stories, catalog-only stories."""
import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from tracker import catalog, db, stories, watchlist
from tracker.fetchers import itunes
from tests.test_stories import art

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    for name in ("Nova Vale", "Kilo Moon", "Iron Lark"):
        watchlist.add(c, name, "core")
    yield c
    c.close()


def rel(pid, title, day, rtype="album"):
    return {"provider_id": pid, "title": title, "record_type": rtype, "release_date": day, "url": f"https://x/{pid}"}


def put(conn, artist_id, provider, releases):
    catalog.store(conn, artist_id, provider, releases, TODAY)
    conn.commit()


def build(conn):
    stories.rebuild(conn, NOW)
    return [dict(r) for r in conn.execute("SELECT * FROM stories ORDER BY id")]


def evidence(conn, story_id, claim="release_date"):
    return sorted((r["kind"], r["provider"], r["value"]) for r in conn.execute(
        "SELECT * FROM story_evidence WHERE story_id=? AND claim=?", (story_id, claim)))


def test_title_key():
    assert catalog.title_key("Glass Hours - Single") == "glass hours"
    assert catalog.title_key("GLASS HOURS (Deluxe Edition)") == "glass hours"
    assert catalog.title_key("Glass Hours [Bonus Tracks]") == "glass hours"
    assert catalog.title_key("Glass Hours: Part 2") == "glass hours part 2"
    assert catalog.title_key("Dead Glass Hours") != catalog.title_key("Glass Hours")


class Fake:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body or {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, time.monotonic()))
        r = requests.Response()
        r.status_code, r._content = self.status, json.dumps(self.body).encode()
        return r


def test_itunes_parses_collections_and_spaces_calls():
    body = {"results": [
        {"wrapperType": "artist", "artistId": 1},
        {"wrapperType": "collection", "collectionId": 11, "collectionName": "Glass Hours", "trackCount": 12,
         "releaseDate": "2026-11-13T08:00:00Z", "collectionViewUrl": "https://music.example/11"},
        {"wrapperType": "collection", "collectionId": 12, "collectionName": "Static - Single", "trackCount": 1,
         "releaseDate": "2026-10-22T07:00:00Z"},
        {"wrapperType": "collection", "collectionId": 13, "collectionName": "Night Drive - EP", "trackCount": 5,
         "releaseDate": "2026-09-01T07:00:00Z"},
        {"wrapperType": "collection", "collectionId": 14, "collectionName": "Two Songs", "trackCount": 2,
         "releaseDate": "2026-09-01T07:00:00Z"}]}
    s = Fake(body=body)
    client = itunes.ItunesClient(s, min_interval=0.05)
    out = client.releases(1)
    out2 = client.releases(1)
    assert [(r["title"], r["record_type"], r["release_date"]) for r in out] == [
        ("Glass Hours", "album", "2026-11-13"), ("Static - Single", "single", "2026-10-22"),
        ("Night Drive - EP", "ep", "2026-09-01"), ("Two Songs", "single", "2026-09-01")]
    assert out == out2 and s.calls[1][2] - s.calls[0][2] >= 0.045
    assert s.calls[0][1]["country"] == "US" and s.calls[0][1]["sort"] == "recent"
    with pytest.raises(requests.RequestException):
        itunes.ItunesClient(Fake(status=429), min_interval=0).releases(1)


def test_itunes_find_artist_is_exact():
    s = Fake(body={"results": [{"artistName": "Nova Vale Band", "artistId": 5}, {"artistName": "nova vale", "artistId": 6}]})
    assert itunes.ItunesClient(s, min_interval=0).find_artist("Nova Vale") == 6
    assert itunes.ItunesClient(Fake(body={"results": []}), min_interval=0).find_artist("Nova Vale") is None


class FakeDeezer:
    def __init__(self, releases):
        self.data = releases

    def find_artist(self, name):
        return {"id": 100 + len(name)}

    def releases(self, deezer_id):
        return self.data


class FakeItunes:
    def __init__(self, releases, fail_after=None):
        self.data, self.fail_after, self.n = releases, fail_after, 0

    def find_artist(self, name):
        return 7

    def releases(self, itunes_id):
        self.n += 1
        if self.fail_after is not None and self.n > self.fail_after:
            raise requests.RequestException("iTunes rate limit (429)")
        return self.data


def test_poll_stores_recent_releases_and_stops_on_a_rate_limit(conn):
    dz = FakeDeezer([rel("d1", "Glass Hours", "2026-10-23"), rel("d0", "Ancient", "2020-01-01")])
    it = FakeItunes([rel("i1", "Glass Hours", "2026-10-23")], fail_after=2)
    out = catalog.poll(conn, limit=10, deezer_client=dz, itunes_client=it, today=TODAY)
    assert out == {"artists": 2, "new": 5}  # the third got its Deezer releases, then hit the iTunes limit
    rows = conn.execute("SELECT provider, title_key FROM releases ORDER BY provider, artist_id").fetchall()
    assert [tuple(r) for r in rows] == [("deezer", "glass hours")] * 3 + [("itunes", "glass hours")] * 2
    checked = conn.execute("SELECT COUNT(*) FROM artist_identities WHERE releases_checked_at IS NOT NULL").fetchone()[0]
    assert checked == 2 and conn.execute("SELECT itunes_id FROM artist_identities WHERE id=1").fetchone()[0] == 7
    # The next poll starts with the artist that wasn't checked; a repeat isn't "new"
    assert catalog.due_artists(conn, 1)[0]["name"] == "Iron Lark"
    assert catalog.poll(conn, 10, dz, FakeItunes([rel("i1", "Glass Hours", "2026-10-23")]), TODAY)["new"] == 1


def test_announced_album_becomes_scheduled_and_confirmed_with_the_catalog_date(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours", "feed-a", 0)
    watchlist.rematch(conn)
    put(conn, 1, "itunes", [rel("i1", "Glass Hours", "2026-11-13")])
    [s] = build(conn)
    assert (s["release_status"], s["verification"], s["release_date"]) == ("scheduled", "confirmed", "2026-11-13")
    assert evidence(conn, s["id"]) == [("catalog", "Apple Music (iTunes)", "2026-11-13")]


def test_article_and_catalog_dates_disagreeing_is_conflicting_with_both_values(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours, Out November 6", "feed-a", 0)
    watchlist.rematch(conn)
    put(conn, 1, "itunes", [rel("i1", "Glass Hours", "2026-11-13")])
    [s] = build(conn)
    assert s["verification"] == "conflicting" and s["release_date"] == "2026-11-06"  # the article's, as first stated
    assert evidence(conn, s["id"]) == [("article", "Feed-A", "2026-11-06"), ("catalog", "Apple Music (iTunes)", "2026-11-13")]


def test_a_day_apart_across_time_zones_is_not_a_conflict(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours, Out November 13", "feed-a", 0)
    watchlist.rematch(conn)
    put(conn, 1, "itunes", [rel("i1", "Glass Hours", "2026-11-12")])
    put(conn, 1, "deezer", [rel("d1", "Glass Hours", "2026-11-13")])
    [s] = build(conn)
    assert s["verification"] == "confirmed"


def test_no_catalog_entry_leaves_an_announcement_alone(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours", "feed-a", 0)
    watchlist.rematch(conn)
    put(conn, 2, "itunes", [rel("i9", "Glass Hours", "2026-10-01")])  # same title, another artist, old: no story
    [s] = build(conn)
    assert (s["release_status"], s["verification"], s["origin"]) == ("announced", "unverified", "news")


def test_released_story_matches_a_single_by_its_catalog_name(conn):
    art(conn, "Glass Hours – Song by Nova Vale", "feed-a", 0)
    watchlist.rematch(conn)
    put(conn, 1, "itunes", [rel("i1", "Glass Hours - Single", "2026-10-22", "single")])
    put(conn, 1, "deezer", [rel("d1", "Glass Hours", "2026-10-23", "single")])
    [s] = build(conn)  # matched: no separate catalog story
    assert (s["release_status"], s["verification"], s["origin"]) == ("released", "confirmed", "news")


def test_catalog_only_stories_for_releases_no_feed_covered(conn):
    put(conn, 2, "deezer", [rel("d1", "Static", "2026-10-23", "single"), rel("d2", "Old One", "2026-10-01", "single"),
                            rel("d3", "Best Of", "2026-10-22", "compile")])
    put(conn, 2, "itunes", [rel("i1", "Static - Single", "2026-10-23", "single")])
    put(conn, 3, "itunes", [rel("i2", "Long Night", "2027-01-15", "album")])
    got = {s["key"]: s for s in build(conn)}
    single = got["2:catalog:static"]
    assert single["title"] == "Kilo Moon released the single “Static”" and single["story_type"] == "single_release"
    assert (single["release_status"], single["verification"], single["origin"]) == ("released", "confirmed", "catalog")
    assert single["rule"] == "catalog: listed on Apple Music (iTunes) and Deezer (no feed covered it)"
    assert single["last_seen"] == "2026-10-23T04:00:00+00:00"
    pre = got["3:catalog:long night"]
    assert pre["title"] == "Iron Lark: album “Long Night” is listed for 2027-01-15"
    assert (pre["story_type"], pre["release_status"]) == ("album_announcement", "scheduled")
    assert len(got) == 2  # the 3-week-old single and the compilation make no story
    assert conn.execute("SELECT COUNT(*) FROM story_articles").fetchone()[0] == 0


def test_muted_artists_get_no_catalog_stories(conn):
    put(conn, 2, "deezer", [rel("d1", "Static", "2026-10-23", "single")])
    watchlist.update(conn, 2, {"muted": True})
    assert build(conn) == []


def test_old_releases_are_pruned(conn):
    catalog.store(conn, 1, "deezer", [rel("d1", "Glass Hours", "2026-10-20")], TODAY)
    assert catalog.store(conn, 1, "deezer", [rel("d0", "Ancient", "2026-01-01")], TODAY) == 0
    catalog.poll(conn, 0, today=date(2027, 3, 1))  # limit 0: nothing polled
    catalog.poll(conn, 1, FakeDeezer([]), FakeItunes([]), date(2027, 3, 1))
    assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0


def test_feature_tags_are_ignored_for_matching_and_guest_spots_are_labelled(conn):
    assert catalog.title_key("Night Drive (Remix) [feat. Kilo Moon]") == "night drive remix"
    assert catalog.title_key("Static (with Kilo Moon & Iron Lark)") == "static"
    assert catalog.title_key("Static feat. Kilo Moon") == "static"
    assert catalog.featured("Static (feat. Echo Bay & Kilo Moon)", "Kilo Moon")
    assert not catalog.featured("Static (feat. Echo Bay)", "Kilo Moon")
    # A headline about the remix matches the catalog entry that carries the feature tag
    art(conn, "Night Drive (Remix) – Song by Echo Bay featuring Nova Vale", "feed-a", 0)
    watchlist.rematch(conn)
    put(conn, 1, "itunes", [rel("i1", "Night Drive (Remix) [feat. Nova Vale] - Single", "2026-10-20", "single")])
    put(conn, 2, "itunes", [rel("i2", "Static (feat. Kilo Moon) - Single", "2026-10-22", "single")])
    got = {s["origin"]: s for s in build(conn)}
    assert got["news"]["verification"] == "confirmed" and got["news"]["release_status"] == "released"
    assert got["catalog"]["title"] == "Kilo Moon is featured on “Static”"
    assert got["catalog"]["story_type"] == "collaboration"


def test_a_release_named_in_a_news_headline_is_evidence_there_not_a_second_card(conn):
    art(conn, "Nova Vale Announces New Album Glass Hours: Hear “Static”", "feed-a", 0)
    watchlist.rematch(conn)
    put(conn, 1, "itunes", [rel("i1", "Static - Single", "2026-10-22", "single")])
    [s] = build(conn)
    assert s["origin"] == "news" and s["release_status"] == "announced"  # it's about the album, still unlisted
    assert evidence(conn, s["id"]) == [("catalog", "Apple Music (iTunes)", "2026-10-22")]
