"""Music news M7: artist discovery, daily snapshots and the momentum rules (tracker/discovery.py)."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tracker import api, catalog, config, db, discovery, drafts, llm, music, watchlist
from tests.test_stories import T0, art, build

NOW = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)


class FakeLastfm:
    def __init__(self, listeners=None):
        self.calls = []
        self.listeners = listeners or {}

    def similar(self, artist, limit=20):
        self.calls.append(("similar", artist))
        return {"Nova Vale": [{"name": "Echo Bay", "match": 0.9, "mbid": "mb-echo", "url": "https://last.fm/echo"},
                              {"name": "Kilo Moon", "match": 0.8, "mbid": None, "url": None},  # already followed
                              {"name": "Glass Hours", "match": 0.4, "mbid": None, "url": None}],
                "Kilo Moon": [{"name": "Echo Bay", "match": 0.7, "mbid": "mb-echo", "url": None}]}.get(artist, [])

    def tag_top(self, tag, limit=50):
        self.calls.append(("tag", tag))
        return [{"name": "Static Bloom", "mbid": None, "url": None, "rank": 3}] if tag == "hyperpop" else []

    def info(self, artist):
        self.calls.append(("info", artist))
        n = self.listeners.get(artist)
        return {"name": artist, "mbid": None, "url": None, "listeners": n, "playcount": None, "tags": ["hyperpop"]} if n else None


class FakeDeezer:
    def __init__(self):
        self.calls = 0

    def find_artist(self, name):
        self.calls += 1
        return {"id": 77, "nb_fan": 5000} if name == "Echo Bay" else None

    def fans(self, artist_id):
        self.calls += 1
        return 5000


class FakeLB:
    def listens(self, mbid):
        return None


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(config, "DISCOVERY_TAGS", ["hyperpop", "plugg"])
    c = db.connect(":memory:")
    for name in ("Nova Vale", "Kilo Moon"):
        watchlist.add(c, name, "core")
    yield c
    c.close()


def put(conn, key, provider, metric, values):
    """values: {days before NOW: value}"""
    for back, v in values.items():
        conn.execute("INSERT INTO artist_metrics VALUES (?,?,?,?,?)",
                     (key, provider, metric, v, (NOW.date() - timedelta(days=back)).isoformat()))
    conn.commit()


def test_guests_on_a_release():
    assert discovery.guests("Static (feat. Echo Bay & Glass Hours)") == ["Echo Bay", "Glass Hours"]
    assert discovery.guests("Night Drive ft. Soft Arcade") == ["Soft Arcade"]
    assert discovery.guests("Night Drive") == []


def test_candidates_come_with_evidence_and_skip_artists_you_follow(conn):
    catalog.store(conn, conn.execute("SELECT id FROM artist_identities WHERE name='Kilo Moon'").fetchone()[0], "deezer",
                  [{"provider_id": "1", "title": "Static (feat. Iron Lark)", "record_type": "single",
                    "release_date": "2026-10-16", "url": None}], date(2026, 10, 23))
    fm = FakeLastfm()
    assert discovery.refresh(conn, fm, NOW) == {"refreshed": True, "candidates": 4, "new": 4}
    listing = discovery.listing(conn)["candidates"]
    names = [c["name"] for c in listing]
    assert names[0] == "Echo Bay" and "Kilo Moon" not in names  # similar to two of yours: the best evidence
    assert set(names) == {"Echo Bay", "Iron Lark", "Glass Hours", "Static Bloom"}
    echo = listing[0]
    assert echo["why"] == ["Similar to Kilo Moon, Nova Vale on Last.fm"] and echo["similar_to"] == ["Kilo Moon", "Nova Vale"]
    iron = next(c for c in listing if c["name"] == "Iron Lark")
    assert iron["why"] == ["Featured on Kilo Moon's “Static”"]
    assert next(c for c in listing if c["name"] == "Static Bloom")["why"] == ["One of Last.fm's top hyperpop artists"]
    # Weekly: a second refresh is skipped; a forced one is served from the week-long cache
    assert discovery.refresh(conn, fm, NOW + timedelta(days=1)) == {"refreshed": False}
    before = len(fm.calls)
    discovery.refresh(conn, fm, NOW + timedelta(days=1), force=True)
    assert len(fm.calls) == before

    # Your calls stick, and accepting adds them to the watchlist (their news starts matching)
    assert discovery.decide(conn, echo["id"], "watch", NOW)["status"] == "accepted"
    row = conn.execute("SELECT tier, origin, mbid FROM artist_identities WHERE name='Echo Bay'").fetchone()
    assert tuple(row) == ("watch", "related", "mb-echo")
    discovery.decide(conn, iron["id"], "dismiss", NOW)
    assert {c["name"] for c in discovery.listing(conn)["candidates"]} == {"Glass Hours", "Static Bloom"}
    assert [c["name"] for c in discovery.listing(conn, "dismissed")["candidates"]] == ["Iron Lark"]
    with pytest.raises(discovery.DiscoveryError):
        discovery.decide(conn, iron["id"], "love")


def test_momentum_needs_samples_a_base_and_two_signals(conn):
    put(conn, "a", "deezer", "fans", {2: 5000, 0: 6000})  # two observations: still collecting
    put(conn, "b", "deezer", "fans", {9: 300, 5: 400, 0: 900})  # tiny base: no percentages
    put(conn, "c", "deezer", "fans", {8: 10000, 4: 10500, 0: 11000})  # ~+8.75%/week
    put(conn, "c", "listenbrainz", "listens", {8: 1000, 4: 2000, 0: 3000})  # growing, but supplementary
    put(conn, "d", "deezer", "fans", {8: 10000, 4: 10500, 0: 11000})
    put(conn, "d", "news", "mentions", {7: 1, 0: 3})  # 3 outlets this week vs 1
    m = discovery.momentum(conn)
    assert not m["a"]["ready"] and m["a"]["value"] is None and m["a"]["signals"][0]["status"] == "collecting"
    assert m["b"]["signals"][0]["status"] == "too_small" and m["b"]["value"] is None
    assert m["c"]["ready"] and m["c"]["growing"] == 1 and not m["c"]["two_signals"] and m["c"]["value"] == 60
    assert m["d"]["two_signals"] and m["d"]["value"] >= 85
    assert "Deezer fans +8.8% a week" in m["d"]["summary"] and "3 outlets in the last 7 days (vs 1" in m["d"]["summary"]


def test_snapshots_once_a_day_in_slices(conn):
    discovery.refresh(conn, FakeLastfm(), NOW)
    fm, dz = FakeLastfm(listeners={"Nova Vale": 40000, "Echo Bay": 9000}), FakeDeezer()
    assert discovery.snapshot_due(conn, NOW, limit=3, clients=(fm, dz, FakeLB())) == 3
    assert discovery.snapshot_due(conn, NOW, limit=10, clients=(fm, dz, FakeLB())) == 2  # 2 followed + 3 candidates
    assert discovery.snapshot_due(conn, NOW, limit=10, clients=(fm, dz, FakeLB())) == 0  # done for today
    got = {(r["artist_key"], r["provider"]): r["value"] for r in conn.execute("SELECT * FROM artist_metrics")}
    assert got[("novavale", "lastfm")] == 40000 and got[("echobay", "deezer")] == 5000
    echo = conn.execute("SELECT deezer_id, tags FROM artist_candidates WHERE name='Echo Bay'").fetchone()
    assert echo["deezer_id"] == 77 and json.loads(echo["tags"]) == ["hyperpop"]
    assert discovery.snapshot_due(conn, NOW + timedelta(days=1), limit=10, clients=(fm, dz, FakeLB())) == 5


def test_mentions_count_distinct_outlets_for_watchlist_and_candidates(conn, monkeypatch):
    monkeypatch.setattr(discovery, "_now", lambda: T0 + timedelta(days=1))
    discovery.refresh(conn, FakeLastfm(), T0)
    art(conn, "Echo Bay Shares New Single Night Drive", source="feed-a")
    art(conn, "Echo Bay Announces Debut Album", source="feed-b", hours=1)
    art(conn, "Nova Vale Announces New Album Glass Hours", source="feed-a", hours=2)
    build(conn)
    discovery.mentions(conn, T0 + timedelta(days=1))
    got = {r["artist_key"]: r["value"] for r in conn.execute("SELECT * FROM artist_metrics WHERE metric='mentions'")}
    assert got["echobay"] == 2 and got["novavale"] == 1 and got["kilomoon"] == 0


def test_a_story_gets_its_artists_momentum(conn, monkeypatch):
    art(conn, "Nova Vale Announces New Album Glass Hours")
    build(conn)
    put(conn, "novavale", "deezer", "fans", {8: 10000, 4: 10500, 0: 11000})
    put(conn, "novavale", "news", "mentions", {7: 0, 0: 2})
    monkeypatch.setattr(discovery, "_now", lambda: NOW)
    story = music._story(conn, conn.execute("SELECT id FROM stories").fetchone()["id"])
    got = music.evaluate(story, music.context(conn, NOW))
    mom = next(c for c in got["components"] if c["key"] == "momentum")
    assert mom["value"] >= 85 and mom["why"].startswith("Growing on two signals (experimental)")
    assert got["coverage"] > 0.85


def test_an_artists_to_watch_idea_drafts_from_the_evidence(conn, monkeypatch):
    discovery.refresh(conn, FakeLastfm(), NOW)
    ids = [c["id"] for c in discovery.listing(conn)["candidates"]][:3]
    with pytest.raises(discovery.DiscoveryError):
        discovery.make_idea(conn, ids[:1])
    alert_id = discovery.make_idea(conn, ids, NOW)
    assert discovery.make_idea(conn, list(reversed(ids)), NOW) == alert_id  # same artists, same idea
    facts = json.loads(conn.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()["facts"])
    assert facts["format"] == "emerging_artists" and facts["artists_to_watch"][0] == {
        "artist": "Echo Bay", "why": "Similar to Kilo Moon, Nova Vale on Last.fm"}
    assert set(facts["because"]) == {"Nova Vale", "Kilo Moon"} and facts["momentum"]["two_signals"] is False
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, alert_id, angle="news"),)).fetchone()
    assert d["model"] == "template" and json.loads(d["warnings"]) == []
    assert "Echo Bay. Similar to Kilo Moon, Nova Vale on Last.fm." in d["script"]
    # Nothing is "blowing up" without two signals
    assert any("blowing up" in w for w in llm.check_facts({"hook": "These artists are blowing up", "script": "", "caption": ""}, facts))


def test_discover_api(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    monkeypatch.setattr(config, "DISCOVERY_TAGS", ["hyperpop"])
    c = db.connect(path)
    watchlist.add(c, "Nova Vale", "core")
    discovery.refresh(c, FakeLastfm(), NOW)
    c.close()
    client = TestClient(api.app)
    got = client.get("/api/discover").json()
    assert [x["name"] for x in got["candidates"]][0] == "Echo Bay" and got["rules"]["min_days"] == 7
    assert client.get("/api/discover?status=maybe").status_code == 400
    first, second = (x["id"] for x in got["candidates"][:2])
    assert client.post(f"/api/discover/{first}/decide", json={"action": "nope"}).status_code == 400
    assert client.post("/api/discover/999/decide", json={"action": "dismiss"}).status_code == 404
    assert client.post("/api/discover/idea", json={"ids": [first]}).status_code == 400
    r = client.post("/api/discover/idea", json={"ids": [first, second]})
    assert r.status_code == 201
    assert client.post(f"/api/discover/{first}/decide", json={"action": "adjacent"}).json()["status"] == "accepted"
    assert any(w["name"] == "Echo Bay" and w["tier"] == "adjacent" for w in client.get("/api/watchlist").json())
