from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from tracker import api, config, db, detectors, instagram, posters

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SHOW_DAY = (datetime.now(timezone.utc) + timedelta(days=60)).date()
FACTS = {"format": "presale", "artist": "Nova Vale",
         "shows": [{"artist": "Nova Vale", "date": "Sat", "venue": "Echo Hall", "url": "https://t/e1"}]}


def ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def post(mid, caption, days_ago=1, media_type="IMAGE"):
    return {"id": mid, "timestamp": ts(days_ago), "media_type": media_type, "caption": caption,
            "media_url": f"https://scontent.cdninstagram.com/{mid}.jpg", "permalink": f"https://www.instagram.com/p/{mid}/"}


class Resp:
    def __init__(self, body=None, content=b"", status=200, headers=None):
        self.body, self.content, self.status_code = body, content, status
        self.headers = headers or {}

    def json(self):
        if self.body is None:
            raise ValueError("no json")
        return self.body

    def iter_content(self, size):
        return iter([self.content])

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class FakeMeta:
    """Graph API pages per handle (newest first, then older by cursor) and the CDN images."""
    def __init__(self, pages, image_status=200):
        self.pages, self.image_status, self.calls = pages, image_status, []

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append((url, params, headers))
        if "cdninstagram" in url:
            return Resp(content=JPEG, status=self.image_status)
        assert "access_token" not in (params or {}) and headers["Authorization"].startswith("Bearer ")
        if url.endswith("/me/accounts"):
            return Resp({"data": [{"instagram_business_account": {"id": "999"}}]})
        fields = params["fields"]
        handle = fields.split("username(")[1].split(")")[0]
        if handle not in self.pages:
            return Resp({"error": {"message": "Invalid user id", "error_user_msg": f"The user with username: {handle} cannot be found."}})
        cursor = fields.split(".after(")[1].split(")")[0] if ".after(" in fields else "0"
        pages = self.pages[handle]
        i = int(cursor)
        media = {"data": pages[i]}
        if i + 1 < len(pages):
            media["paging"] = {"cursors": {"after": str(i + 1)}, "next": "https://graph.facebook.com/next"}
        return Resp({"business_discovery": {"media": media}}, headers={"x-app-usage": '{"call_count":5}'})


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(posters, "POSTER_DIR", tmp_path / "posters")
    monkeypatch.setattr(config, "META_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(config, "INSTAGRAM_VENUES", {"Echo Hall": "echohall"})
    monkeypatch.setattr(instagram, "PAGE_SIZE", 1)  # every fake page is "full", so there may be more
    c = db.connect(tmp_path / "ig.db")
    c.execute("INSERT INTO venues (id, name) VALUES ('v1', 'Echo Hall')")
    c.execute("""INSERT INTO events (id, name, url, local_date, status, venue_id, active, first_seen, last_seen)
                 VALUES ('e1', 'Nova Vale', 'https://t/e1', ?, 'onsale', 'v1', 1, 'x', 'x')""", (SHOW_DAY.isoformat(),))
    c.execute("INSERT INTO attractions (id, name, first_seen) VALUES ('a1', 'Nova Vale', 'x')")
    c.execute("INSERT INTO event_attractions (event_id, attraction_id, position) VALUES ('e1', 'a1', 0)")
    c.commit()
    yield c
    c.close()


def show(conn, artist_handles=()):
    s = instagram._shows(conn, ["e1"])[0]
    s["artist_handles"] = list(artist_handles)
    return s


def row(handle, caption, days_ago=1):
    return {"handle": handle, "caption": caption, "posted_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()}


def test_date_patterns():
    r = instagram._date_re("2027-04-26")
    for text in ("on april 26th!", "apr. 26", "4/26", "04.26", "26 april", "the 26th of apr", "(4/26/27)"):
        assert r.search(text), text
    for text in ("4/261", "14/26", "april 2", "april 266"):
        assert not r.search(text), text


def test_match_rules(conn):
    s = show(conn, ["novavale"])
    day = f"{SHOW_DAY.strftime('%B')} {SHOW_DAY.day}".lower()
    assert instagram.match(row("echohall", f"JUST ANNOUNCED: Nova Vale on {day}!"), s) == instagram._STRONG
    assert instagram.match(row("echohall", "JUST ANNOUNCED: Nova Vale, tickets Friday"), s) == "names the artist"
    assert instagram.match(row("echohall", "Supernova Valentine night"), s) is None  # whole words only
    assert instagram.match(row("echohall", "Photos from last night w/ @novavale"), s) == "tags the artist"
    assert instagram.match(row("novavale", "PHILLY we're coming for you"), s).startswith("the artist's post about Philly")
    assert instagram.match(row("novavale", "The Big Tour: tickets on sale now"), s) == instagram._TOUR
    assert instagram.match(row("novavale", "new single out now"), s) is None
    assert instagram.match(row("echohall", "Nova Vale tonight", days_ago=-70), s) is None  # posted after the show
    assert instagram.match(row("echohall", "Nova Vale", days_ago=config.INSTAGRAM_HISTORY_DAYS + 1), s) is None


def test_short_names_need_the_date(conn):
    conn.execute("UPDATE attractions SET name='Mogi'")
    s = show(conn)
    assert instagram.match(row("echohall", "Mogi and friends"), s) is None
    day = f"{SHOW_DAY.month}/{SHOW_DAY.day}"
    assert instagram.match(row("echohall", f"{day}: Mogi and friends"), s) == instagram._STRONG


def test_another_visit_needs_the_date(conn):
    conn.execute("""INSERT INTO events (id, name, url, local_date, status, venue_id, active, first_seen, last_seen)
                    VALUES ('e2', 'Nova Vale', 'https://t/e2', ?, 'onsale', 'v1', 1, 'x', 'x')""",
                 ((SHOW_DAY + timedelta(days=7)).isoformat(),))
    s = show(conn)
    assert s["repeats"] and not s["run"]
    assert instagram.match(row("echohall", "Nova Vale tomorrow!"), s) is None  # which visit? can't tell
    assert instagram.match(row("echohall", f"Nova Vale {SHOW_DAY.month}/{SHOW_DAY.day}"), s) == instagram._STRONG


def test_back_to_back_nights_share_an_undated_announcement(conn):
    conn.execute("""INSERT INTO events (id, name, url, local_date, status, venue_id, active, first_seen, last_seen)
                    VALUES ('e2', 'Nova Vale', 'https://t/e2', ?, 'onsale', 'v1', 1, 'x', 'x')""",
                 ((SHOW_DAY + timedelta(days=1)).isoformat(),))
    s = show(conn)
    assert s["run"] and not s["repeats"]
    assert instagram.match(row("echohall", "Nova Vale tickets on sale Friday"), s) == \
        "names the artist (for the run of nights, not one date)"
    assert instagram.match(row("echohall", f"Nova Vale {SHOW_DAY.month}/{SHOW_DAY.day}"), s) == instagram._STRONG


def test_refresh_reads_venues_then_history_and_saves_matching_posters(conn):
    old = [post("o1", f"JUST ANNOUNCED Nova Vale {SHOW_DAY.month}/{SHOW_DAY.day}", days_ago=40)]
    meta = FakeMeta({"echohall": [[post("n1", "Trivia night"), post("n2", "Karaoke")], old,
                                  [post("x1", "ancient", days_ago=config.INSTAGRAM_HISTORY_DAYS + 5)],
                                  [post("x2", "never read")]]})
    out = instagram.refresh(conn, session=meta)
    assert out == {"calls": 3, "posters_saved": 1}  # newest page, then older ones until past the history window
    acct = conn.execute("SELECT * FROM instagram_accounts").fetchone()
    assert acct["history_done"] == 1 and acct["history_cursor"] is None and acct["error"] is None
    assert conn.execute("SELECT COUNT(*) FROM instagram_posts").fetchone()[0] == 4  # x2 never read
    saved = conn.execute("SELECT file FROM instagram_posts WHERE media_id='o1'").fetchone()["file"]
    assert (posters.POSTER_DIR / saved).read_bytes() == JPEG
    assert instagram.refresh(conn, session=meta)["calls"] == 0  # fresh: nothing to read


def test_hourly_cap_and_meta_usage_stop_calls(conn, monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_HOURLY_CAP", 2)
    meta = FakeMeta({"echohall": [[post("n1", "x")], [post("o1", "y")], [post("o2", "z")]]})
    assert instagram.refresh(conn, session=meta)["calls"] == 1  # user id + newest page = the 2 allowed
    assert instagram.calls_this_hour(conn) == 2
    monkeypatch.setattr(config, "INSTAGRAM_HOURLY_CAP", 60)
    conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES ('instagram:usage', '80', ?)",
                 (datetime.now(timezone.utc).isoformat(),))
    with pytest.raises(instagram.Budget):
        instagram.fetch_account(conn, "echohall", meta, older=True)


def test_a_missing_account_is_remembered(conn):
    meta = FakeMeta({})
    assert instagram.fetch_account(conn, "nobody", meta) == 0
    a = conn.execute("SELECT * FROM instagram_accounts WHERE handle='nobody'").fetchone()
    assert "cannot be found" in a["error"] and not instagram.stale(conn, "nobody")


class FakeMB:
    def __init__(self):
        self.calls = []

    def find_artist(self, name):
        self.calls.append(("find", name))
        return "mb-1", False

    def links(self, mbid):
        self.calls.append(("links", mbid))
        return [{"type": "homepage", "url": "https://nova.example"},
                {"type": "instagram", "url": "https://www.instagram.com/novavale/"}]


def test_artist_handle_from_musicbrainz_is_cached(conn):
    mb = FakeMB()
    assert instagram.artist_handle(conn, "Nova Vale", "a1", mb) == "novavale"
    assert instagram.artist_handle(conn, "Nova Vale", "a1", mb) == "novavale"
    assert mb.calls == [("find", "Nova Vale"), ("links", "mb-1")]


def test_musicbrainz_links_read_instagram():
    from tracker.fetchers import musicbrainz

    class S:
        def get(self, url, params=None, headers=None, timeout=None):
            return Resp({"relations": [
                {"type": "social network", "url": {"resource": "https://www.instagram.com/novavale/"}},
                {"type": "social network", "url": {"resource": "https://twitter.com/novavale"}},
                {"type": "youtube", "url": {"resource": "https://youtube.com/@nova"}}]})
    links = musicbrainz.MusicBrainzClient(session=S(), min_interval=0).links("mb-1")
    assert [l["type"] for l in links] == ["instagram", "youtube"]


def test_idea_posters_include_instagram_and_download(conn, monkeypatch, tmp_path):
    detectors.save_alerts(conn, [{"key": "p", "kind": "presale_soon", "title": "t", "score": 50, "facts": FACTS,
                                  "event_ids": ["e1"]}])
    conn.commit()
    day = f"{SHOW_DAY.month}/{SHOW_DAY.day}"
    meta = FakeMeta({"echohall": [[post("v1", f"JUST ANNOUNCED Nova Vale {day}", days_ago=3)]],
                     "novavale": [[post("t1", "THE BIG TOUR: tickets on sale", days_ago=30),
                                   post("t2", "Philly see you soon", days_ago=10)]]})
    monkeypatch.setattr(instagram.requests, "get", meta.get)
    monkeypatch.setattr(instagram, "artist_handle", lambda c, name, aid=None, mb=None: "novavale")
    monkeypatch.setattr(instagram, "handle_known", lambda c, name: True)
    monkeypatch.setattr(api, "conn", lambda: db.connect(tmp_path / "ig.db"))
    cl = TestClient(api.app)
    got = cl.get("/api/alerts/1/posters").json()
    assert [(p["kind"], p["credit"], p["match"]) for p in got] == [
        ("instagram", "@echohall", instagram._STRONG),
        ("instagram", "@novavale", "the artist's post about Philly"),
        ("instagram", "@novavale", instagram._TOUR)]
    assert all(p["saved"] and p["image"] for p in got)
    assert any("may not list" in w for w in got[2]["warnings"])
    r = cl.get(got[0]["download"])
    assert r.status_code == 200 and r.content == JPEG
    assert f"{SHOW_DAY.isoformat()} Nova Vale - Echo Hall.jpg" in unquote(r.headers["content-disposition"])
    assert cl.get("/api/instagram/nope").status_code == 404
    # the second open reads nothing new from Meta
    n = len(meta.calls)
    cl.get("/api/alerts/1/posters")
    assert len(meta.calls) == n


def test_off_without_a_token(conn, monkeypatch):
    monkeypatch.setattr(config, "META_ACCESS_TOKEN", "")
    assert instagram.refresh(conn) == {"skipped": "no META_ACCESS_TOKEN"}
    assert instagram.for_shows(conn, ["e1"]) == {}


def test_expired_image_link_is_recorded(conn):
    meta = FakeMeta({"echohall": [[post("v1", f"Nova Vale {SHOW_DAY.month}/{SHOW_DAY.day}")]]}, image_status=403)
    instagram.fetch_account(conn, "echohall", meta)
    p = conn.execute("SELECT * FROM instagram_posts").fetchone()
    assert instagram.save(conn, p, meta)["error"] == "image link expired: open the post"
