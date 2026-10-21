"""Fetch → store → detect, without touching the network."""
from datetime import datetime, timezone

import pytest
import responses

from tracker import config, db, detectors, store
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)  # a Wednesday


@pytest.fixture
def conn():
    return db.connect(":memory:")


def ingest(conn, raws, baseline=None):
    run = store.start_run(conn, "tm")
    if baseline is None:
        baseline = store.is_first_run(conn, "tm")
    events = [tm.normalize(r) for r in raws if not tm.is_junk(r)]
    counts = store.upsert_events(conn, run, events, baseline=baseline)
    store.finish_run(conn, run, fetched=len(events), api_calls=0, ok=True)
    return run, counts


# ---------- fetcher ----------

def page(events, number, total_pages, total):
    return {"_embedded": {"events": events}, "page": {"number": number, "totalPages": total_pages, "totalElements": total}}


@responses.activate
def test_fetch_pages_through_results():
    responses.get(tm.BASE_URL, json=page([tm_event(event_id="a")], 0, 2, 2))
    responses.get(tm.BASE_URL, json=page([tm_event(event_id="b")], 1, 2, 2))
    client = tm.TicketmasterClient(api_key="k", min_interval=0)
    events = client.fetch_events(now=NOW, days=30)
    assert {e["id"] for e in events} == {"a", "b"}
    assert client.calls == 2
    assert responses.calls[0].request.params["radius"] == str(config.RADIUS_MILES)


@responses.activate
def test_fetch_splits_window_when_too_many_results():
    responses.get(tm.BASE_URL, json=page([], 0, 10, 1500))         # full window: too big
    responses.get(tm.BASE_URL, json=page([tm_event(event_id="a")], 0, 1, 1))  # first half
    responses.get(tm.BASE_URL, json=page([tm_event(event_id="b")], 0, 1, 1))  # second half
    client = tm.TicketmasterClient(api_key="k", min_interval=0)
    events = client.fetch_events(now=NOW, days=30)
    assert {e["id"] for e in events} == {"a", "b"}
    assert client.calls == 3


@responses.activate
def test_fetch_respects_budget():
    responses.get(tm.BASE_URL, json=page([tm_event()], 0, 5, 5))
    client = tm.TicketmasterClient(api_key="k", min_interval=0, budget=2)
    with pytest.raises(tm.BudgetExceeded):
        client.fetch_events(now=NOW, days=30)


@responses.activate
def test_fetch_retries_on_429(monkeypatch):
    monkeypatch.setattr(tm.time, "sleep", lambda s: None)
    responses.get(tm.BASE_URL, status=429)
    responses.get(tm.BASE_URL, json=page([tm_event(event_id="a")], 0, 1, 1))
    events = tm.TicketmasterClient(api_key="k", min_interval=0).fetch_events(now=NOW, days=30)
    assert [e["id"] for e in events] == ["a"]


def test_normalize_and_junk_filter():
    raw = tm_event(name="Mt. Joy", presales=[("Artist Presale", "2026-10-22T14:00:00Z", "2026-10-23T03:00:00Z")],
                   support=["Opener"])
    n = tm.normalize(raw)
    assert n["local_date"] == "2026-11-08" and n["price_min"] == 45.0
    assert [a["name"] for a in n["attractions"]] == ["Mt. Joy", "Opener"]
    assert n["presales"][0]["name"] == "Artist Presale"
    assert tm.is_junk(tm_event(name="Mt. Joy - Parking Pass"))
    assert not tm.is_junk(raw)


def test_normalize_cleans_names():
    raw = tm_event(name="The Jayhawks - All Ages (under 16 with adult)", artist="The Jayhawks")
    raw["_embedded"]["venues"][0]["name"] = "TLO Temple "
    n = tm.normalize(raw)
    assert (n["name"], n["attractions"][0]["name"], n["venue"]["name"]) == ("The Jayhawks", "The Jayhawks", "TLO Temple")
    assert tm.clean_event_name("Mike Dawes (21 and Over)") == "Mike Dawes"
    assert tm.clean_event_name("Boyz II Men (30th Anniversary)") == "Boyz II Men (30th Anniversary)"


def test_normalize_handles_missing_fields():
    raw = tm_event(price=None)
    del raw["sales"]
    del raw["_embedded"]
    raw["dates"]["start"] = {"localDate": "2026-11-08", "timeTBA": True}
    n = tm.normalize(raw)
    assert n["price_min"] is None and n["venue"] is None and n["local_time"] is None and n["presales"] == []


# ---------- change tracking ----------

def test_first_run_is_baseline_then_changes_are_logged(conn):
    a = tm_event(event_id="a", name="Band A")
    run1, counts1 = ingest(conn, [a])
    assert counts1 == {}  # baseline: nothing is "new"

    a2 = tm_event(event_id="a", name="Band A", status="rescheduled", price=(50.0, 85.0),
                  presales=[("Fan Club", "2026-10-22T14:00:00Z", None)])
    b = tm_event(event_id="b", name="Band B")
    run2, counts2 = ingest(conn, [a2, b])
    assert counts2 == {"status": 1, "price": 1, "presale_added": 1, "new": 1}

    _, counts3 = ingest(conn, [a2, b])  # nothing changed
    assert counts3 == {}

    _, counts4 = ingest(conn, [b])  # A disappeared from the feed
    assert counts4 == {"removed": 1}
    assert conn.execute("SELECT active FROM events WHERE id='a'").fetchone()[0] == 0


# ---------- detectors ----------

def test_new_tour_detected_only_for_notable_new_artists(conn):
    ingest(conn, [tm_event(event_id="old", artist="Old Band")])
    run, _ = ingest(conn, [
        tm_event(event_id="old", artist="Old Band"),
        tm_event(event_id="n1", name="Big Star", venue="Wells Fargo Center", upcoming=80, date="2027-03-01"),
        tm_event(event_id="n2", name="Big Star", venue="Wells Fargo Center", upcoming=80, date="2027-03-02"),
        tm_event(event_id="tiny", name="Tiny Act", venue="Some Bar", upcoming=1),
    ])
    alerts = detectors.detect_new_tours(conn, run, "2026-10-21")
    assert len(alerts) == 1
    a = alerts[0]
    assert a["facts"]["artist"] == "Big Star"
    assert [s["date"] for s in a["facts"]["shows"]] == ["Mon, Mar 1", "Tue, Mar 2"]
    assert a["facts"]["already_had_philly_dates"] is False


def test_sales_soon_one_alert_per_artist_listing_all_windows(conn):
    presale = [("Citi Presale", "2026-10-22T14:00:00Z", "2026-10-23T03:00:00Z"),
               ("VIP Packages Onsale", "2026-10-22T14:00:00Z", None)]
    ingest(conn, [
        tm_event(event_id="x1", name="Star", date="2027-02-01", presales=presale, onsale="2026-10-24T14:00:00Z"),
        tm_event(event_id="x2", name="Star", date="2027-02-02", presales=presale, onsale="2026-10-24T14:00:00Z"),
        tm_event(event_id="far", name="Later", presales=[("P", "2026-11-30T14:00:00Z", None)]),
        tm_event(event_id="tiny", name="Tiny", venue="Some Bar", upcoming=1,
                 presales=[("Artist Presale", "2026-10-22T14:00:00Z", None)]),
    ])
    [alert] = detectors.detect_sales_soon(conn, NOW, "2026-10-21")  # not "Later" (too far), not "Tiny" (too small)
    f = alert["facts"]
    assert f["artist"] == "Star" and len(f["shows"]) == 2
    assert f["sale_opens"] == "Thu, Oct 22 at 10:00 AM ET"  # 14:00Z = 10 AM EDT
    assert [s["name"] for s in f["all_sales"]] == ["Citi Presale", "General public on-sale"]  # VIP skipped


def test_sales_already_open_dont_alert(conn):
    ingest(conn, [tm_event(event_id="x", name="Star", onsale="2026-10-01T14:00:00Z")])
    assert detectors.detect_sales_soon(conn, NOW, "2026-10-21") == []


def test_weekend_roundup_picks_varied_genres(conn):
    raws = [tm_event(event_id=f"r{i}", name=f"Rock {i}", date="2026-10-24", genre="Rock") for i in range(4)]
    raws += [tm_event(event_id="h", name="Rapper", date="2026-10-25", genre="Hip-Hop/Rap"),
             tm_event(event_id="next", name="Next Week", date="2026-10-31")]
    ingest(conn, raws)
    [alert] = detectors.detect_weekend(conn, NOW.date())
    genres = [s["genre"] for s in alert["facts"]["shows"]]
    assert genres.count("Rock") == 2 and "Hip-Hop/Rap" in genres
    assert alert["key"] == "weekend:2026-10-23"
    assert detectors.detect_weekend(conn, datetime(2026, 10, 19).date()) == []  # Monday: too early


def test_stacked_month(conn):
    raws = [tm_event(event_id=f"c{i}", name=f"Country {i}", genre="Country", date=f"2026-11-{10 + i}",
                     venue="The Met Philadelphia", upcoming=50) for i in range(6)]
    ingest(conn, raws)
    [alert] = detectors.detect_stacked_months(conn, NOW.date())
    assert alert["key"] == "stacked:2026-11:Country"
    assert alert["facts"]["notable_show_count"] == 6


def test_status_change_alert(conn):
    ingest(conn, [tm_event(event_id="s", name="Star", venue="Wells Fargo Center")])
    run, _ = ingest(conn, [tm_event(event_id="s", name="Star", venue="Wells Fargo Center", status="postponed")])
    [alert] = detectors.detect_status_changes(conn, run, "2026-10-21")
    assert alert["facts"]["change"] == "postponed"


def test_alerts_are_deduplicated(conn):
    a = {"key": "k1", "kind": "weekend", "title": "t", "score": 1, "facts": {}}
    assert len(detectors.save_alerts(conn, [a])) == 1
    assert detectors.save_alerts(conn, [a]) == []


def test_stacked_month_skips_current_month_after_the_15th(conn):
    raws = [tm_event(event_id=f"o{i}", name=f"Rock {i}", date=f"2026-10-{22 + i}", venue="The Met Philadelphia",
                     upcoming=50) for i in range(6)]
    ingest(conn, raws)
    assert detectors.detect_stacked_months(conn, NOW.date()) == []  # Oct 21: October is nearly over
    assert len(detectors.detect_stacked_months(conn, NOW.date().replace(day=10))) == 1
