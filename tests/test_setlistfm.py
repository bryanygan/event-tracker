"""setlist.fm history: finding the last Philly-area show, and how comebacks show up in alerts and drafts."""
import json
from datetime import date, datetime, timezone

import pytest
import responses

from tracker import db, detectors, drafts, llm, store, templates
from tracker.fetchers import setlistfm as sf
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event

TODAY = date(2026, 10, 21)
PHILLY = {"lat": 39.9524, "long": -75.1636}
CAMDEN = {"lat": 39.9259, "long": -75.1196}      # ~3 mi: counts as Philly area
NYC = {"lat": 40.7143, "long": -74.006}          # ~80 mi: doesn't


def setlist(d, venue, coords):
    return {"eventDate": d, "venue": {"name": venue, "city": {"name": "x", "coords": coords}}}


@pytest.fixture
def client():
    return sf.SetlistFmClient(api_key="k", min_interval=0)


def test_in_area_uses_the_pipeline_radius():
    assert sf.in_area(PHILLY) and sf.in_area(CAMDEN)
    assert not sf.in_area(NYC) and not sf.in_area(None) and not sf.in_area({"lat": None, "long": None})


@responses.activate
def test_find_mbid_exact_name_only(client):
    responses.get(f"{sf.BASE}/search/artists", json={"artist": [
        {"name": "Papa Roach & Beartooth", "mbid": "wrong"}, {"name": "papa roach", "mbid": "right"}]})
    assert client.find_mbid("Papa Roach") == "right"
    responses.replace(responses.GET, f"{sf.BASE}/search/artists", status=404)  # setlist.fm's "no results"
    assert client.find_mbid("Nobody") is None


@responses.activate
def test_last_area_show_takes_newest_area_show_from_city_search_or_recent_list(client):
    # City search: deep Philly history (fuzzy match includes Camden), plus an upcoming show to ignore.
    responses.get(f"{sf.BASE}/search/setlists", json={"total": 3, "setlist": [
        setlist("14-03-2027", "Xfinity Mobile Arena", PHILLY),
        setlist("07-08-2019", "Metropolitan Opera House", PHILLY),
        setlist("06-10-2018", "BB&T Pavilion", CAMDEN)]})
    # Recent list: a 2022 suburban show the city search missed, and far-away shows.
    responses.get(f"{sf.BASE}/artist/m/setlists", json={"total": 3, "setlist": [
        setlist("01-08-2026", "MSG", NYC), setlist("20-05-2022", "Keswick Theatre", {"lat": 40.08, "long": -75.15}),
        setlist("01-07-2021", "Somewhere", NYC)]})
    found = client.last_area_show("m", TODAY)
    assert (found["date"], found["venue"]) == (date(2022, 5, 20), "Keswick Theatre")
    assert client.calls == 2


@responses.activate
def test_last_area_show_none_found(client):
    responses.get(f"{sf.BASE}/search/setlists", status=404)  # setlist.fm's "no results"
    responses.get(f"{sf.BASE}/artist/m/setlists", json={"total": 2, "setlist": [
        setlist("01-08-2026", "MSG", NYC), setlist("05-05-2024", "Boston", {"lat": 42.36, "long": -71.06})]})
    assert client.last_area_show("m", TODAY) == {"date": None, "venue": None, "checked_back_to": date(2024, 5, 5)}


@responses.activate
def test_enrich_history_stores_results_and_caches(tmp_path, client):
    conn = db.connect(tmp_path / "h.db")
    r = store.start_run(conn, "x")
    store.upsert_events(conn, r, [tm.normalize(tm_event(name="Hozier", date="2027-03-14"))], baseline=True)
    responses.get(f"{sf.BASE}/search/artists", json={"artist": [{"name": "Hozier", "mbid": "hz"}]})
    responses.get(f"{sf.BASE}/search/setlists", json={"total": 1, "setlist": [
        setlist("19-11-2019", "Metropolitan Opera House", PHILLY)]})
    responses.get(f"{sf.BASE}/artist/hz/setlists", json={"total": 0, "setlist": []})
    assert sf.enrich_history(conn, client, today=TODAY) == 1
    row = conn.execute("SELECT * FROM attractions WHERE name='Hozier'").fetchone()
    assert (row["setlistfm_mbid"], row["last_area_show_date"], row["last_area_show_venue"]) == \
        ("hz", "2019-11-19", "Metropolitan Opera House")
    assert sf.enrich_history(conn, client, today=TODAY) == 0  # cached


def test_enrich_history_skipped_without_key(tmp_path):
    assert sf.enrich_history(db.connect(tmp_path / "n.db"), sf.SetlistFmClient(api_key="")) == 0


def _presale_alert(conn, last_show=None, venue="Metropolitan Opera House"):
    presale = [("Artist Presale", "2026-10-22T14:00:00Z", None)]
    r = store.start_run(conn, "ticketmaster")
    store.upsert_events(conn, r, [tm.normalize(tm_event(event_id="h", name="Hozier", date="2027-03-14",
                                                        venue="Xfinity Mobile Arena", upcoming=70, presales=presale))],
                        baseline=True)
    if last_show:
        conn.execute("UPDATE attractions SET last_area_show_date=?, last_area_show_venue=? WHERE name='Hozier'",
                     (last_show, venue))
    [alert] = detectors.detect_sales_soon(conn, datetime(2026, 10, 21, 16, tzinfo=timezone.utc), "2026-10-21")
    return alert


def test_comeback_presale_alert_script_caption_onscreen(tmp_path):
    a = _presale_alert(db.connect(tmp_path / "c.db"), "2019-11-19")
    h = a["facts"]["history"]
    assert (h["last_philly_show"], h["years_since_last_philly_show"], h["is_comeback"]) == ("Nov 19, 2019", 6, True)
    assert a["title"].startswith("Back after 6 years: ")
    f = a["facts"]
    script = templates.script_for(f)
    assert "Hozier is playing Philly for the first time since 2019." in script
    assert templates.caption_for(f).startswith("First Philly show since 2019 (per setlist.fm)!")
    assert "FIRST PHILLY SHOW SINCE 2019" in llm.onscreen_lines(f)
    assert llm.check_facts({"script": script, "caption": templates.caption_for(f)}, f) == []


def test_recent_show_or_no_history_is_not_a_comeback(tmp_path):
    recent = _presale_alert(db.connect(tmp_path / "r.db"), "2024-08-07")  # 2 years: below the 3-year bar
    assert recent["facts"]["history"]["is_comeback"] is False
    assert "first time since" not in templates.script_for(recent["facts"])
    unknown = _presale_alert(db.connect(tmp_path / "u.db"))
    assert unknown["facts"]["history"] is None and not unknown["title"].startswith("Back after")


def test_comeback_draft_carries_a_verify_note(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "d.db")
    a = _presale_alert(conn, "2019-11-19")
    detectors.save_alerts(conn, [a])
    monkeypatch.setattr(llm, "generate", lambda facts, model=None, script=None, caption=None: {
        "hook": "Hozier is back in Philly", "hashtags": [], "model": "m", "script": script, "caption": caption})
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, 1),)).fetchone()
    assert any("setlist.fm" in w for w in json.loads(d["warnings"]))


def test_comeback_fallback_hook_and_no_repeated_name(tmp_path):
    f = _presale_alert(db.connect(tmp_path / "f.db"), "2019-11-19")["facts"]
    assert templates.fallback_hook(f) == "Hozier's first Philly show since 2019"
    script = templates.script_for(f)
    assert "The Artist Presale opens" in script and script.count("Hozier") == 1
