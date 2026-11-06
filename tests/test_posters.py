from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from tracker import api, db, detectors, posters, showcase

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
FACTS = {"format": "presale", "artist": "Nova Vale",
         "shows": [{"artist": "Nova Vale", "date": "Sat, Mar 6", "venue": "Echo Hall", "url": "https://t/1"}]}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def iter_content(self, size):
        return iter([self.body[i:i + size] for i in range(0, len(self.body), size)])

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, body=JPEG):
        self.body, self.calls = body, []

    def get(self, url, **kw):
        self.calls.append(url)
        return FakeResponse(self.body)


def _event(c, eid, image, source=None, name="Nova Vale", date="2027-03-06", status="onsale"):
    c.execute("INSERT OR IGNORE INTO venues (id, name) VALUES ('v1', 'Echo Hall')")
    c.execute("""INSERT INTO events (id, name, url, image_url, local_date, status, venue_id, source, first_seen, last_seen)
                 VALUES (?,?,?,?,?,?,'v1',?,'x','x')""", (eid, name, f"https://t/{eid}", image, date, status, source))


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(posters, "POSTER_DIR", tmp_path / "posters")
    c = db.connect(tmp_path / "p.db")
    yield c
    c.close()


def test_kind_of():
    assert posters.kind_of("https://static.tixr.com/a.jpg", "underground-arts") == "poster"
    assert posters.kind_of("https://s1.ticketm.net/dam/e/ead/x_SOURCE", None) == "event_image"
    assert posters.kind_of("https://s1.ticketm.net/dam/a/ac2/x_SOURCE", None) == "artist_photo"
    assert posters.kind_of("https://s1.ticketm.net/dam/c/1/x.jpg", None) == "generic"
    assert posters.kind_of(None, None) is None


def test_fetch_saves_once_and_again_when_the_image_changes(conn):
    _event(conn, "e1", "https://static.tixr.com/a.jpg", source="underground-arts")
    s = FakeSession()
    row = posters.fetch(conn, "e1", s)
    assert row["file"] and row["kind"] == "poster" and (posters.POSTER_DIR / row["file"]).read_bytes() == JPEG
    posters.fetch(conn, "e1", s)
    assert len(s.calls) == 1  # cached
    conn.execute("UPDATE events SET image_url='https://static.tixr.com/b.jpg' WHERE id='e1'")
    posters.fetch(conn, "e1", s)
    assert s.calls[-1].endswith("b.jpg")
    assert posters.file_for(conn, "e1")[1] == "image/jpeg"


def test_fetch_refuses_non_images_and_artist_photos(conn):
    _event(conn, "e1", "https://static.tixr.com/a.jpg", source="underground-arts")
    row = posters.fetch(conn, "e1", FakeSession(b"<html>not an image</html>"))
    assert row["file"] is None and "not a PNG" in row["error"]
    _event(conn, "e2", "https://s1.ticketm.net/dam/a/1/x_SOURCE")
    assert posters.fetch(conn, "e2", FakeSession()) is None


def test_warnings_when_the_listing_moved(conn):
    _event(conn, "e1", "https://static.tixr.com/a.jpg", source="underground-arts",
           name="Nova Vale ** Relocated to Glass Hall", status="rescheduled")
    conn.execute("INSERT INTO changes (event_id, kind, old_value, new_value, at) VALUES ('e1','date','2027-03-01','2027-03-06','x')")
    e = posters._event(conn, "e1")
    w = posters.warnings(conn, e, {"seen": '{"venue": "Old Room", "name": "Nova Vale"}'})
    assert any("2027-03-01 → 2027-03-06" in x for x in w) and any("rescheduled" in x for x in w)
    assert any("moved" in x for x in w) and any("Old Room → Echo Hall" in x for x in w)
    assert any("bill changed" in x for x in w)


def test_for_alert_api_and_image(conn, monkeypatch, tmp_path):
    _event(conn, "e1", "https://static.tixr.com/a.jpg", source="underground-arts")
    _event(conn, "e2", "https://s1.ticketm.net/dam/a/1/x_SOURCE", name="Kilo Moon")
    detectors.save_alerts(conn, [{"key": "p", "kind": "presale_soon", "title": "t", "score": 50, "facts": FACTS,
                                  "event_ids": ["e1", "e2"]}])
    conn.commit()
    monkeypatch.setattr(posters.requests, "get", FakeSession().get)
    monkeypatch.setattr(api, "conn", lambda: db.connect(tmp_path / "p.db"))
    cl = TestClient(api.app)
    got = cl.get("/api/alerts/1/posters").json()
    assert [(p["kind"], p["saved"], p["credit"]) for p in got] == [("poster", True, "Echo Hall"),
                                                                   ("artist_photo", False, None)]
    r = cl.get("/api/posters/e1")
    assert r.status_code == 200 and r.content == JPEG
    assert cl.get("/api/posters/e2").status_code == 404
    assert cl.get("/api/alerts/99/posters").status_code == 404


def test_prefetch_takes_open_event_ideas_within_the_limit(conn, monkeypatch):
    for i in range(3):
        _event(conn, f"e{i}", f"https://static.tixr.com/{i}.jpg", source="underground-arts")
    detectors.save_alerts(conn, [{"key": "p", "kind": "presale_soon", "title": "t", "score": 50, "facts": FACTS,
                                  "event_ids": ["e0", "e1", "e2"]}])
    s = FakeSession()
    monkeypatch.setattr(posters.requests, "Session", lambda: s)
    assert posters.prefetch(conn, limit=2, pause=0) == 2
    assert posters.prefetch(conn, limit=5, pause=0) == 1  # only the one not saved yet


def test_showcase_drops_posters():
    assert "image_url" not in showcase.clean({"image_url": "https://static.tixr.com/a.jpg", "name": "x"})


def test_ticketmaster_show_keeps_the_venues_poster(conn):
    from tracker.fetchers import venue_calendars as vc
    cal = {"local_date": "2027-03-06", "venue": {"name": "Echo Hall"}, "name": "Nova Vale",
           "attractions": [{"name": "Nova Vale"}], "image_url": "https://static.tixr.com/a.jpg"}
    tm = {"local_date": "2027-03-06", "venue": {"name": "The Echo Hall"}, "name": "Nova Vale: Big Tour",
          "attractions": [{"name": "Nova Vale"}]}
    kept, dropped = vc.drop_duplicates([cal], [tm])
    assert kept == [] and dropped == 1 and tm["venue_image_url"] == "https://static.tixr.com/a.jpg"

    _event(conn, "e1", "https://s1.ticketm.net/dam/a/1/x_SOURCE")
    conn.execute("UPDATE events SET venue_image_url='https://static.tixr.com/a.jpg' WHERE id='e1'")
    s = FakeSession()
    assert posters.fetch(conn, "e1", s)["kind"] == "poster" and s.calls == ["https://static.tixr.com/a.jpg"]


def _picture(path, size, shapes, colour="white"):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 400), colour)
    d = ImageDraw.Draw(img)
    for box, fill in shapes:
        d.rectangle(box, fill=fill)
    img.resize(size).save(path, "JPEG")


def _entry(key, event_id, kind="poster", match=None, date="2027-03-06"):
    return {"key": key, "event_id": event_id, "kind": kind, "label": key, "date": date, "match": match,
            "source_url": f"https://x/{key}", "saved": True, "image": f"/i/{key}", "download": f"/d/{key}", "error": None}


def test_copies_of_one_picture_show_once_and_blank_covers_count_as_unsaved(conn):
    posters.POSTER_DIR.mkdir(parents=True)
    tour = [((0, 0, 400, 80), "red"), ((40, 120, 360, 300), "navy"), ((100, 330, 300, 380), "gold")]
    _picture(posters.POSTER_DIR / "site.jpg", (600, 600), tour)
    _picture(posters.POSTER_DIR / "ig_1.jpg", (1080, 1080), tour)  # the venue's Instagram copy, bigger
    _picture(posters.POSTER_DIR / "other.jpg", (600, 600), [((0, 300, 400, 400), "green"), ((150, 20, 250, 250), "black")])
    _picture(posters.POSTER_DIR / "ig_2.jpg", (640, 1136), [], colour="black")  # a video's black cover
    (posters.POSTER_DIR / "ig_3.jpg").write_bytes(JPEG)  # unreadable: kept as it is
    entries = [_entry("e1", "e1"), _entry("ig:1", "e1", "instagram", "names the date"),
               _entry("e2", "e2", date="2027-03-07"), _entry("ig:2", "e2", "instagram", "names the date"),
               _entry("ig:3", "e1", "instagram", "names the date"), _entry("ig:3", "e2", "instagram", "names the date")]
    files = {"e1": "site.jpg", "ig:1": "ig_1.jpg", "e2": "other.jpg", "ig:2": "ig_2.jpg", "ig:3": "ig_3.jpg"}
    got = posters._merge_copies(conn, entries, files)
    assert [p["key"] for p in got] == ["ig:1", "e2", "ig:2", "ig:3"]
    assert [c["event_id"] for c in got[0]["copies"]] == ["e1"]  # the biggest copy kept, the site's listed on it
    assert got[2]["saved"] is False and got[2]["image"] is None and "blank" in got[2]["error"]
    assert [c["event_id"] for c in got[3]["copies"]] == ["e2"]  # one post matched to both nights
    assert conn.execute("SELECT COUNT(*) FROM poster_prints").fetchone()[0] == 5  # printed once, cached
    assert posters._merge_copies(conn, [_entry("e1", "e1"), _entry("e2", "e2")], files)[1]["key"] == "e2"


def test_download_is_named_after_the_show(conn, monkeypatch, tmp_path):
    _event(conn, "e1", "https://static.tixr.com/a.jpg", source="underground-arts", name="Nova Vale: Live / Tour")
    posters.fetch(conn, "e1", FakeSession())
    monkeypatch.setattr(api, "conn", lambda: db.connect(tmp_path / "p.db"))
    r = TestClient(api.app).get("/api/posters/e1?download=1")
    assert r.status_code == 200 and "2027-03-06 Nova Vale Live Tour - Echo Hall.jpg" in unquote(r.headers["content-disposition"])
