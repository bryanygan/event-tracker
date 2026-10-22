"""Venue calendar reader: JSON-LD parsing, cleanup, dedupe against Ticketmaster, and safe integration."""
import json

import pytest
import responses

from tracker import api, config, db, store
from tracker import run as pipeline
from tracker.fetchers import ticketmaster as tm
from tracker.fetchers import venue_calendars as vc
from tests.factory import tm_event

VENUE = {"key": "ua", "name": "Underground Arts", "url": "https://ua.example/", "city": "Philadelphia"}


def tixr_item(name, start="2027-01-08T00:30:00+00:00", tixr_id="211409", price=25, availability="InStock"):
    url = f"https://www.tixr.com/groups/ua/events/x-{tixr_id}"
    return {"@context": "https://schema.org", "@type": "Event", "name": name, "startDate": start, "url": url,
            "performer": "Organization",
            "offers": {"@type": "Offer", "url": url, "price": price, "availability": availability}}


def page(*items):
    return "<html><script type='application/ld+json'>%s</script></html>" % json.dumps(list(items))


def test_utc_times_become_philly_local_dates():
    ev = vc.normalize(tixr_item("Band"), VENUE)
    assert (ev["local_date"], ev["local_time"]) == ("2027-01-07", "19:30:00")  # 00:30Z Jan 8 = 7:30 PM Jan 7


def test_status_markers_html_entities_and_ids():
    ev = vc.normalize(tixr_item("MARIS &amp; Friends ** SOLD OUT **"), VENUE)
    assert (ev["name"], ev["status"], ev["id"]) == ("MARIS & Friends", "soldout", "cal:ua:tixr-211409")
    assert vc.normalize(tixr_item("Sophie Truax * Canceled *"), VENUE)["status"] == "cancelled"
    assert vc.normalize(tixr_item("Band", availability="SoldOut"), VENUE)["status"] == "soldout"
    dice = {"@type": "MusicEvent", "name": "ODD MOB", "startDate": "2027-02-14T02:30:00Z",
            "eventStatus": "https://schema.org/EventPostponed",
            "offers": {"url": "https://dice.fm/event/l8pqax"}, "performer": {"@type": "MusicGroup", "name": "ODD MOB"}}
    ev = vc.normalize(dice, VENUE)
    assert (ev["id"], ev["status"], ev["price_min"]) == ("cal:ua:dice-l8pqax", "postponed", None)


def test_headliner_from_performers_or_name():
    ev = vc.normalize(tixr_item("Dead Wrong: 2nd Annual Holiday Show"), VENUE)
    assert ev["name"] == "Dead Wrong: 2nd Annual Holiday Show" and ev["attractions"][0]["name"] == "Dead Wrong"
    ev = vc.normalize(tixr_item("Movements w/ Balance & Composure"), VENUE)
    assert [a["name"] for a in ev["attractions"]] == ["Movements", "Balance & Composure"]
    item = {**tixr_item("Porkroll PHL"), "performer": [{"name": "DJ Janky"}, {"name": "Other"}]}
    assert [a["name"] for a in vc.normalize(item, VENUE)["attractions"]] == ["DJ Janky", "Other"]


def test_junk_filter():
    for name in ("CLOSED FOR PRIVATE EVENT", "GOLDEN: A K-Pop Kids Party!", "Pancakes & Booze", "Trivia Night"):
        assert vc.is_junk(name), name
    assert not vc.is_junk("Clap Your Hands Say Yeah")


@responses.activate
def test_fetch_venue_respects_robots_and_reports_empty_pages():
    responses.get("https://ua.example/robots.txt", body="User-agent: *\nDisallow: /")
    with pytest.raises(vc.SourceError, match="robots"):
        vc.fetch_venue(VENUE)
    responses.replace(responses.GET, "https://ua.example/robots.txt", status=404)
    responses.get("https://ua.example/", body="<html>redesigned, no data</html>")
    with pytest.raises(vc.SourceError, match="no schema.org events"):
        vc.fetch_venue(VENUE)
    responses.replace(responses.GET, "https://ua.example/", body=page(tixr_item("Band"), tixr_item("Trivia Night", tixr_id="2")))
    assert [e["name"] for e in vc.fetch_venue(VENUE)] == ["Band"]


def test_ticketmaster_copy_wins():
    cal = vc.normalize(tixr_item("Movements w/ Balance & Composure"), VENUE)
    same = tm.normalize(tm_event(name="Movements", date="2027-01-07", venue="Underground Arts"))
    other_room = tm.normalize(tm_event(name="Movements", date="2027-01-07", venue="Union Transfer"))
    other_act = tm.normalize(tm_event(name="Totally Different", date="2027-01-07", venue="Underground Arts"))
    assert vc.drop_duplicates([cal], [same]) == ([], 1)
    assert vc.drop_duplicates([cal], [other_room, other_act]) == ([cal], 0)


@pytest.fixture
def two_venues(monkeypatch):
    monkeypatch.setattr(config, "VENUE_CALENDARS", [VENUE, {**VENUE, "key": "mb", "name": "MilkBoy", "url": "https://mb.example/"}])


def _run(conn, monkeypatch, results):
    """One pipeline ingest with fake TM data and fake venue results ({key: list | SourceError})."""
    def fake_fetch(venue, session=None):
        r = results[venue["key"]]
        if isinstance(r, Exception):
            raise r
        return [vc.normalize(i, venue) for i in r]
    monkeypatch.setattr(vc, "fetch_venue", fake_fetch)
    run_id = store.start_run(conn, "ticketmaster")
    tm_events = [tm.normalize(tm_event(event_id="tm1", name="Big", date="2027-02-01"))]
    cal, first, failed = pipeline.fetch_calendars(conn, run_id, tm_events)
    counts = store.upsert_events(conn, run_id, tm_events + cal, baseline=False, baseline_prefixes=first,
                                 protected_prefixes=failed)
    store.finish_run(conn, run_id, fetched=1, api_calls=0, ok=True)
    return counts


def test_first_read_is_baseline_then_new_shows_count_and_failures_keep_shows(tmp_path, monkeypatch, two_venues):
    conn = db.connect(tmp_path / "v.db")
    counts = _run(conn, monkeypatch, {"ua": [tixr_item("A", tixr_id="1")], "mb": [tixr_item("B", tixr_id="2")]})
    assert counts.get("new") == 1  # only Ticketmaster's tm1; venue shows on their first read aren't "new"

    counts = _run(conn, monkeypatch, {"ua": [tixr_item("A", tixr_id="1"), tixr_item("C", tixr_id="3")],
                                      "mb": vc.SourceError("fetch failed: timeout")})
    assert counts == {"new": 1}  # C is new; MilkBoy's B is NOT marked removed just because its site failed
    assert conn.execute("SELECT active FROM events WHERE id='cal:mb:tixr-2'").fetchone()[0] == 1

    problems = api.source_problems(conn)
    assert problems == [{"source": "MilkBoy", "error": "fetch failed: timeout"}]


def test_replaying_cache_never_touches_venue_sites(tmp_path, monkeypatch, two_venues):
    monkeypatch.setattr(vc, "fetch_venue", lambda *a, **k: pytest.fail("must not fetch"))
    conn = db.connect(tmp_path / "c.db")
    events, first, failed = pipeline.fetch_calendars(conn, 1, [], from_cache=True)
    assert events == [] and failed == ("cal:ua:", "cal:mb:")
