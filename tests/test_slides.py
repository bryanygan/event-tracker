"""9:16 slide manifests: built from facts only, deterministic, edits survive a rebuild only where safe."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from tracker import api, db, detectors, llm, slides

SHOW = {"artist": "Nova Vale", "date": "Sat, Nov 7", "time": "8:00 PM", "venue": "Union Transfer",
        "url": "https://tm.example/e1", "genre": "R&B", "status": "onsale", "support": "Kilo Moon"}
PRESALE = {"format": "presale", "artist": "Nova Vale", "sale_name": "ARTIST PRESALE",
           "sale_opens": "Thu, Oct 22 at 10:00 AM ET", "hours_until_open": 18, "your_plays": 40,
           "all_sales": [{"name": "ARTIST PRESALE", "opens": "Thu, Oct 22 at 10:00 AM ET"},
                         {"name": "General public on-sale", "opens": "Fri, Oct 23 at 10:00 AM ET"}],
           "shows": [SHOW],
           "history": {"is_comeback": True, "last_philly_show_year": 2017, "years_since_last_philly_show": 9,
                       "last_philly_show": "May 2, 2017", "last_philly_venue": "TLA"}}
WEEKEND = {"format": "weekend", "weekend": "Fri, Oct 23 – Sun, Oct 25",
           "shows": [{**SHOW, "artist": f"Act {i}", "url": f"https://tm.example/w{i}", "genre": "Pop"} for i in range(10)]}
GAME = {"format": "big_game", "artist": "Sixers", "team": "Philadelphia 76ers", "opponent": "Boston Celtics",
        "show": {"artist": "Sixers vs. Celtics", "date": "Sat, Oct 24", "time": "7:30 PM",
                 "venue": "Xfinity Mobile Arena", "kind": "game", "url": "https://tm.example/g1"}}
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake bytes").decode()


def _text(manifest: dict) -> str:
    return " ".join(v for s in manifest["slides"] for v in s["fields"].values())


@pytest.mark.parametrize("facts", [PRESALE, WEEKEND, GAME])
def test_built_from_facts_only_and_deterministic(facts):
    m = slides.build(facts)
    assert m == slides.build(json.loads(json.dumps(facts)))
    assert llm.check_facts({"script": _text(m)}, facts) == []  # no date, time or number that isn't in the facts
    assert [s["kind"] for s in m["slides"]][0] == "title" and m["slides"][-1]["kind"] == "cta"


def test_presale_deck():
    m = slides.build(PRESALE)
    kinds = [s["kind"] for s in m["slides"]]
    assert kinds == ["title", "show", "sales", "cta"] and m["theme"] == "plum"
    title, show, sales = (s["fields"] for s in m["slides"][:3])
    assert title["kicker"] == "BACK IN PHILLY" and title["sub"] == "First Philly show since 2017"
    assert "setlist.fm" in title["note"]
    assert show == {"date": "Sat, Nov 7", "time": "8:00 PM", "artist": "Nova Vale", "venue": "Union Transfer",
                    "extra": "with Kilo Moon"}
    assert sales["lines"].split("\n") == ["Artist Presale: Thu, Oct 22 at 10:00 AM ET",
                                          "General public on-sale: Fri, Oct 23 at 10:00 AM ET"]
    assert "40" not in _text(m)  # listening data never lands on a slide


def test_long_roundups_are_capped_and_counted():
    m = slides.build(WEEKEND)
    assert sum(s["kind"] == "show" for s in m["slides"]) == slides.MAX_SHOWS
    assert m["shows_total"] == 10 and m["slides"][0]["fields"]["headline"] == "10 shows this weekend"


def test_games_get_their_own_theme():
    m = slides.build(GAME)
    assert m["theme"] == "court" and m["slides"][0]["fields"]["headline"] == "Sixers vs. Celtics"


def test_rebuild_keeps_edits_whose_fact_did_not_change():
    old = slides.build(PRESALE)
    sid = old["slides"][1]["id"]
    overrides = {sid: {"artist": "NOVA VALE", "date": "SAT NOV 7!!", "hidden": False},
                 "cta": {"headline": "Tag your crew"}, slides.DECK: {"brand": "@me"}}
    corrected = {**PRESALE, "shows": [{**SHOW, "date": "Sun, Nov 8"}]}  # the date was wrong
    new = slides.build(corrected)
    assert new["slides"][1]["id"] == sid  # the ticket URL keeps the slide's identity
    kept, dropped = slides.rebase(old, overrides, new)
    assert kept[sid] == {"artist": "NOVA VALE", "hidden": False} and kept["cta"] == {"headline": "Tag your crew"}
    assert kept[slides.DECK] == {"brand": "@me"}
    assert len(dropped) == 1 and "SAT NOV 7!!" in dropped[0] and "Sun, Nov 8" in dropped[0]
    shown = slides.apply(new, kept)
    assert shown["slides"][1]["fields"]["date"] == "Sun, Nov 8" and shown["brand"] == "@me"


def test_removed_show_drops_its_edits():
    old = slides.build(WEEKEND)
    sid = old["slides"][2]["id"]
    new = slides.build({**WEEKEND, "shows": WEEKEND["shows"][:1]})
    kept, dropped = slides.rebase(old, {sid: {"artist": "x"}}, new)
    assert kept == {} and "slide removed" in dropped[0]


@pytest.mark.parametrize("bad", [
    {"nope": {"headline": "x"}},
    {"title": {"headline": "x" * 301}},
    {"title": {"color": "red"}},
    {"title": {"hidden": "yes"}},
    {"cta": {"image": {"data_url": PNG, "credit": "me", "rights": "own"}}},
    {"title": {"image": {"data_url": "https://example.com/a.png", "credit": "me", "rights": "own"}}},
    {"title": {"image": {"data_url": "data:image/svg+xml;base64,PHN2Zz4=", "credit": "me", "rights": "own"}}},
    {"title": {"image": {"data_url": PNG, "credit": " ", "rights": "own"}}},
    {"title": {"image": {"data_url": PNG, "credit": "me", "rights": "found it online"}}},
    {"deck": {"brand": "x" * 61}},
    "not a dict",
])
def test_validate_rejects(bad):
    with pytest.raises(slides.SlidesError):
        slides.validate(slides.build(PRESALE), bad)


def test_validate_requires_one_visible_slide():
    base = slides.build(PRESALE)
    with pytest.raises(slides.SlidesError):
        slides.validate(base, {s["id"]: {"hidden": True} for s in base["slides"]})


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    c = db.connect(path)
    detectors.save_alerts(c, [{"key": "p", "kind": "presale_soon", "title": "t", "score": 50, "facts": PRESALE}])
    c.close()
    return TestClient(api.app), path


def test_api_save_warn_rebuild_reset(client):
    cl, path = client
    d = cl.get("/api/alerts/1/slides").json()
    assert not d["saved"] and d["facts_changes"] == [] and "own" in d["rights"]
    sid = d["manifest"]["slides"][1]["id"]
    img = {"data_url": PNG, "credit": "Photo: me", "rights": "own"}
    r = cl.put("/api/alerts/1/slides", json={"overrides": {sid: {"date": "SAT 11/7", "image": img}, "sales": {"hidden": True}}})
    assert r.status_code == 200 and r.json()["saved"]
    shown = r.json()["manifest"]["slides"]
    assert shown[1]["fields"]["date"] == "SAT 11/7" and shown[1]["image"]["credit"] == "Photo: me"
    assert shown[1]["edited"] == ["date"] and shown[2]["hidden"]
    assert cl.put("/api/alerts/1/slides", json={"overrides": {"title": {"bogus": 1}}}).status_code == 400

    # a run corrects the date: the saved deck still shows your version, with a warning
    c = db.connect(path)
    c.execute("UPDATE alerts SET facts=? WHERE id=1", (json.dumps({**PRESALE, "shows": [{**SHOW, "date": "Sun, Nov 8"}]}),))
    c.commit()
    c.close()
    d = cl.get("/api/alerts/1/slides").json()
    assert d["facts_changes"] and d["manifest"]["slides"][1]["fields"]["date"] == "SAT 11/7"
    r = cl.post("/api/alerts/1/slides/rebuild").json()
    assert r["facts_changes"] == [] and r["dropped"] and r["manifest"]["slides"][1]["fields"]["date"] == "Sun, Nov 8"
    assert r["manifest"]["slides"][1]["image"]["credit"] == "Photo: me" and r["manifest"]["slides"][2]["hidden"]

    assert not cl.delete("/api/alerts/1/slides").json()["saved"]
    assert cl.get("/api/alerts/99/slides").status_code == 404
    assert cl.put("/api/alerts/99/slides", json={"overrides": {}}).status_code == 404


def test_volatile_facts_dont_flag_a_saved_deck(client):
    cl, path = client
    cl.put("/api/alerts/1/slides", json={"overrides": {"cta": {"headline": "Tag your crew"}}})
    c = db.connect(path)
    c.execute("UPDATE alerts SET facts=? WHERE id=1", (json.dumps({**PRESALE, "hours_until_open": 2, "your_plays": 41}),))
    c.commit()
    c.close()
    assert cl.get("/api/alerts/1/slides").json()["facts_changes"] == []
