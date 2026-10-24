"""P2-14: markets/festivals from Center City District and the waterfront, events you add by hand, comedy, more
teams, and the formats built on them. All names are invented."""
from datetime import date, datetime, timezone

import pytest
import responses
from fastapi.testclient import TestClient

from tracker import api, config, db, detectors, llm, manual_events, store, templates
from tracker import run as pipeline
from tracker.fetchers import experiences as ex
from tracker.fetchers import ticketmaster as tm
from tracker.fetchers.venue_calendars import SourceError
from tracker.scoring import experience_score, upcoming_events
from tests.factory import tm_event

CCD = {"key": "ccd", "name": "Center City District", "kind": "tribe", "url": "https://ccd.example", "base": 50}
DRWC = {"key": "drwc", "name": "Delaware River Waterfront", "kind": "drwc", "url": "https://drwc.example", "base": 60}
WED = date(2026, 11, 18)  # weekend = Fri Nov 20 - Sun Nov 22


def daily(first, last, skip=()):
    """'2026-11-14'..'2026-12-31', every day except `skip` (a market closed on a holiday)."""
    from datetime import timedelta
    d, out = date.fromisoformat(first), []
    while d <= date.fromisoformat(last):
        if d.isoformat() not in skip:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def tribe(title, start, end=None, cost="", values=(), venue="Glass Hours Plaza", all_day=False):
    return {"title": title, "start_date": f"{start} 12:00:00", "end_date": f"{end or start} 21:00:00",
            "all_day": all_day, "url": f"https://ccd.example/event/{ex.slug(title)}/", "cost": cost,
            "cost_details": {"values": list(values)}, "venue": {"venue": venue, "city": "Philadelphia"}}


# ---------- reading the sources ----------

def test_recurring_listings_collapse_into_one_event_with_a_range():
    items = [tribe("Nova Vale Holiday Market", d) for d in daily("2026-11-14", "2026-12-31", skip={"2026-11-26"})]
    items += [tribe("Echo Bay Happy Hour", d) for d in ("2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23")]
    items += [tribe("Canceled &#8211; Open Streets: Echo Bay", "2026-11-22"),
              tribe("Iron Lark Tasting", "2026-11-21", values=["25", "40"]),
              tribe("Soft Arcade Lights", "2026-11-28", cost="Free"),
              tribe("Kilo Moon Fair", "2026-09-01")]  # over: dropped
    by_name = {e["name"]: e for e in ex.collapse_tribe(items, CCD, date(2026, 11, 16))}
    market = by_name["Nova Vale Holiday Market"]
    assert (market["local_date"], market["end_date"], market["id"]) == ("2026-11-14", "2026-12-31", "ccd:nova-vale-holiday-market")
    assert (market["segment"], market["genre"], market["source"]) == ("Experience", "Market", "ccd")
    assert market["price_min"] is None  # no cost listed = nothing said about price
    assert by_name["Open Streets: Echo Bay"]["status"] == "cancelled"
    assert (by_name["Iron Lark Tasting"]["price_min"], by_name["Iron Lark Tasting"]["price_max"]) == (25.0, 40.0)
    assert by_name["Soft Arcade Lights"]["price_min"] == 0.0  # the listing's own cost says Free
    assert "Kilo Moon Fair" not in by_name
    weekly = by_name["Echo Bay Happy Hour"]  # weekly: its next date, not "Nov 2 – Nov 23"
    assert (weekly["local_date"], weekly["end_date"]) == ("2026-11-16", None)


ICS = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20261127T160000Z\r\nDTEND:20261130T040000Z\r\n"
       "SUMMARY:Opening Weekend: Echo Bay Winterfest\r\nLOCATION:Echo Bay Rink\\, 101 Columbus Blvd\r\n"
       "END:VEVENT\r\nEND:VCALENDAR\r\n")


def test_waterfront_ical_dates_are_local_and_late_endings_belong_to_the_day_before():
    [fields] = ex.ics_events(ICS)
    ev = ex.drwc_event(DRWC, "opening-weekend", fields)
    assert (ev["local_date"], ev["local_time"], ev["end_date"]) == ("2026-11-27", "11:00:00", "2026-11-29")
    assert ev["venue"]["name"] == "Echo Bay Rink" and ev["genre"] == "Holiday"
    one_night = ex.drwc_event(DRWC, "x", {"DTSTART": "20270214T000000Z", "DTEND": "20270214T033000Z",
                                          "SUMMARY": "(TICKETED EVENT) Static Fire & Ice Party"})
    assert (one_night["name"], one_night["local_date"], one_night["end_date"]) == ("Static Fire & Ice Party", "2027-02-13", None)


@responses.activate
def test_fetch_checks_robots_and_reads_every_page():
    responses.add(responses.GET, "https://ccd.example/robots.txt", body="User-agent: *\nDisallow: /wp-admin/")
    api_url = "https://ccd.example/wp-json/tribe/events/v1/events"
    responses.add(responses.GET, api_url, json={"events": [tribe("Nova Vale Market", "2026-11-20")], "total_pages": 2})
    responses.add(responses.GET, api_url, json={"events": [tribe("Glass Hours Festival", "2026-11-21")], "total_pages": 2})
    got = ex.fetch_source(CCD, today=date(2026, 11, 16))
    assert sorted(e["name"] for e in got) == ["Glass Hours Festival", "Nova Vale Market"]


@responses.activate
def test_a_disallowed_or_broken_source_is_a_source_error():
    responses.add(responses.GET, "https://ccd.example/robots.txt", body="User-agent: *\nDisallow: /wp-json/")
    with pytest.raises(SourceError, match="robots"):
        ex.fetch_source(CCD, today=date(2026, 11, 16))
    responses.add(responses.GET, "https://drwc.example/robots.txt", status=404)
    responses.add(responses.GET, "https://drwc.example/what-to-do", status=503)
    with pytest.raises(SourceError):
        ex.fetch_source(DRWC, today=date(2026, 11, 16))


def test_a_failed_source_keeps_its_events_and_replays_keep_yours(monkeypatch):
    conn = db.connect(":memory:")
    monkeypatch.setattr(config, "EXPERIENCE_SOURCES", [CCD])

    def boom(source, session=None, today=None):
        raise SourceError("fetch failed: timeout")
    monkeypatch.setattr(ex, "fetch_source", boom)
    events, first, failed = pipeline.fetch_experiences(conn, store.start_run(conn, "ticketmaster"))
    assert (events, failed) == ([], ("ccd:",))
    manual_events.create(conn, name="Kilo Moon Pop-Up", start_date="2099-05-01", venue="Iron Lark Hall")
    events, first, failed = pipeline.fetch_experiences(conn, 1, from_cache=True)
    assert [e["id"] for e in events] == ["man:1"] and "man:" in first and failed == ("ccd:",)


# ---------- scoring and ideas ----------

def _store(conn, events, baseline=True):
    store.upsert_events(conn, store.start_run(conn, "ticketmaster"), events, baseline=baseline)


def test_a_feed_read_for_the_first_time_is_not_just_announced():
    conn = db.connect(":memory:")
    _store(conn, [tm.normalize(team_game("s1", "Philadelphia 76ers", "Miami Heat", "2026-11-19", genre="Basketball"))])
    later = [tm.normalize(r) for r in (team_game("s2", "Philadelphia 76ers", "Utah Jazz", "2026-11-25", genre="Basketball"),
                                       team_game("f1", "Philadelphia Flyers", "Utah Mammoth", "2026-11-26"),
                                       comedy("c1", "Kilo Moon", "2026-12-01"))]
    assert pipeline.new_feed_ids(conn, later) == {"f1", "c1"}  # the Sixers feed was already read
    run = store.start_run(conn, "ticketmaster")
    store.upsert_events(conn, run, later, baseline=False, baseline_ids=pipeline.new_feed_ids(conn, later))
    assert [r["event_id"] for r in conn.execute("SELECT event_id FROM changes WHERE kind='new'")] == ["s2"]


def test_experience_scores_favor_markets_and_festivals_over_routine_listings():
    assert experience_score({"name": "Nova Vale Holiday Market", "source": "ccd"}) == 70
    assert experience_score({"name": "Happy Hour at the Echo Bay Cabin", "source": "ccd"}) == 25
    assert experience_score({"name": "Static Tree Lighting", "source": "drwc"}) == 80
    assert experience_score({"name": "anything", "source": "manual"}) == config.EXPERIENCE_MANUAL_SCORE


def local_events():
    items = [tribe("Nova Vale Holiday Market", d) for d in daily("2026-11-14", "2026-12-31")]
    items += [tribe("Happy Hour at the Echo Bay Cabin", "2026-11-20"), tribe("Glass Hours Festival", "2026-12-30")]
    return ex.collapse_tribe(items, CCD, date(2026, 11, 1))


@pytest.fixture
def local_conn():
    conn = db.connect(":memory:")
    _store(conn, local_events())
    return conn


def test_one_idea_per_notable_experience_starting_soon(local_conn):
    alerts = {a["facts"]["artist"]: a for a in detectors.detect_experiences(local_conn, WED)}
    assert list(alerts) == ["Nova Vale Holiday Market"]  # happy hour scores low; the festival is 6 weeks out
    a = alerts["Nova Vale Holiday Market"]
    assert a["key"] == "experience:ccd:nova-vale-holiday-market:2026" and a["kind"] == "experience"
    show = a["facts"]["show"]
    assert (show["dates"], show["kind"], show["listed_by"]) == ("Sat, Nov 14 – Thu, Dec 31", "experience", "Center City District")
    script = templates.script_for(a["facts"])
    assert script.startswith("Nova Vale Holiday Market is happening from Saturday, November 14 through Thursday, December 31")
    assert "free" not in script.lower() and "$" not in script  # the listing gave no price
    draft = {"hook": "Holiday Market season", "script": script, "caption": templates.caption_for(a["facts"])}
    assert llm.check_facts(draft, a["facts"]) == []
    assert "Market" in templates.hook_names(a["facts"])


def test_free_and_cheap_need_the_facts_to_say_so():
    facts = {"format": "experience", "artist": "Nova Vale Market", "show": {"date": "Sat, Nov 21", "price": None}}
    assert llm.check_facts({"hook": "It's free!", "script": "", "caption": ""}, facts) == ["Not in facts: free"]
    assert llm.check_facts({"hook": "Cheap fun this weekend", "script": "", "caption": ""}, facts) == ["Not in facts: Cheap"]
    assert llm.check_facts({"hook": "Feel free to bring friends", "script": "", "caption": ""}, facts) == []
    named = {"artist": "Free Static", "shows": [{"artist": "Free Static", "date": "Sat, Nov 21"}]}
    assert llm.check_facts({"hook": "Free Static is in Philly", "script": "", "caption": ""}, named) == []
    free = {**facts, "show": {"date": "Sat, Nov 21", "price": "Free"}}
    assert llm.check_facts({"hook": "It's free!", "script": "", "caption": ""}, free) == []


def comedy(event_id, name, d, venue="Echo Bay Comedy Club"):
    raw = tm_event(event_id=event_id, name=name, artist=name, date=d, venue=venue, genre="Comedy", support=[])
    raw["classifications"] = [{"segment": {"name": "Arts & Theatre"}, "genre": {"name": "Comedy"}}]
    return raw


def team_game(event_id, home, opponent, d, genre="Hockey"):
    raw = tm_event(event_id=event_id, name=f"{home} vs. {opponent}", artist=home, date=d, venue="Xfinity Mobile Arena",
                   genre=genre, price=None, support=[opponent])
    raw["classifications"] = [{"segment": {"name": "Sports"}, "genre": {"name": genre}}]
    return raw


def test_things_to_do_mixes_markets_comedy_a_game_and_concerts(local_conn):
    raws = [comedy("c1", "Kilo Moon", "2026-11-21"),
            team_game("f1", "Philadelphia Flyers", "Pittsburgh Penguins", "2026-11-20"),
            team_game("f2", "Philadelphia Flyers", "Columbus Blue Jackets", "2026-11-22"),
            tm_event(event_id="m1", name="Iron Lark", artist="Iron Lark", date="2026-11-21", venue="The Fillmore Philadelphia")]
    _store(local_conn, [tm.normalize(r) for r in raws] + local_events())  # one batch, as in a real run
    [a] = detectors.detect_things_to_do(local_conn, WED)
    kinds = [s.get("kind") for s in a["facts"]["shows"]]
    assert kinds.count("experience") == 1 and kinds.count("comedy") == 1 and kinds.count("game") == 1
    assert a["key"] == "things_to_do:2026-11-20" and "Happy Hour" not in str(a["facts"])
    # the music weekend roundup leaves comedy and markets to this one
    weekend = detectors.detect_weekend(local_conn, WED)
    assert all(s.get("kind") not in ("comedy", "experience") for w in weekend for s in w["facts"]["shows"])


def test_no_things_to_do_without_markets_or_comedy():
    conn = db.connect(":memory:")
    _store(conn, [tm.normalize(tm_event(event_id=f"m{i}", name=f"Act {i}", artist=f"Act {i}", date="2026-11-21"))
                  for i in range(4)])
    assert detectors.detect_things_to_do(conn, WED) == []


def test_comedy_stays_out_of_music_formats_and_deezer():
    conn = db.connect(":memory:")
    _store(conn, [tm.normalize(comedy(f"c{i}", f"Comic {i}", "2026-12-0%d" % (i + 1), venue="Academy of Music"))
                  for i in range(8)])
    assert detectors.detect_stacked_months(conn, date(2026, 11, 10)) == []
    e = upcoming_events(conn, "2026-11-01")[0]
    assert (e["my_plays"], e["niche"], detectors.event_facts(e)["kind"]) == (0, False, "comedy")
    assert tm.is_comedy_event(comedy("x", "Comic", "2026-12-01"))
    assert not tm.is_comedy_event(tm_event(name="Concert"))


# ---------- teams ----------

def test_more_teams_with_their_own_rivals_and_tags():
    conn = db.connect(":memory:")
    _store(conn, [tm.normalize(r) for r in (
        team_game("f1", "Philadelphia Flyers", "Pittsburgh Penguins", "2026-11-19"),
        team_game("f2", "Philadelphia Flyers", "Columbus Blue Jackets", "2026-11-21"),
        team_game("e1", "Philadelphia Eagles", "Dallas Cowboys", "2026-11-22", genre="Football"),
        team_game("u1", "Philadelphia Union", "Inter Miami CF", "2026-11-21", genre="Soccer"))])
    weeks = {a["facts"]["team"]: a for a in detectors.detect_team_week(conn, WED)}
    assert set(weeks) == {"Philadelphia Flyers", "Philadelphia Union"}  # the Eagles get one idea per game instead
    assert [g["artist"] for g in weeks["Philadelphia Flyers"]["facts"]["shows"]] == ["Flyers vs. Penguins", "Flyers vs. Blue Jackets"]
    big = {a["facts"]["team"]: a for a in detectors.detect_big_games(conn, WED)}
    assert set(big) == {"Philadelphia Flyers", "Philadelphia Eagles", "Philadelphia Union"}
    assert big["Philadelphia Eagles"]["title"].startswith("Big game: Eagles vs. Cowboys")  # a rival; any home game alerts
    assert big["Philadelphia Flyers"]["title"].startswith("Big game: Flyers vs. Penguins")
    assert big["Philadelphia Union"]["facts"]["show"]["artist"] == "Union vs. Inter Miami"
    tags = templates.game_hashtags(big["Philadelphia Flyers"]["facts"])
    assert "#NHL" in tags and "#FlyersVsPenguins" in tags and "#NBA" not in tags
    assert "🏒" in templates.caption_for(weeks["Philadelphia Flyers"]["facts"])
    assert templates.script_for(big["Philadelphia Union"]["facts"]).startswith("The Inter Miami are coming to Philly.")


def test_arena_add_ons_carrying_the_team_are_not_games():
    addon = team_game("a", "Philadelphia Flyers", "Utah Mammoth", "2026-11-19")
    addon["classifications"] = [{"segment": {"name": "Miscellaneous"}, "genre": {"name": "Hockey"}}]
    assert not tm.is_tracked_team_event(addon)
    assert tm.is_tracked_team_event(team_game("b", "Philadelphia Flyers", "Utah Mammoth", "2026-11-19"))


def test_opponent_nicknames():
    short = detectors.opponent_short
    assert [short(n) for n in ("Dallas Cowboys", "Columbus Blue Jackets", "Toronto FC", "Inter Miami CF", None)] == \
        ["Cowboys", "Blue Jackets", "Toronto", "Inter Miami", "TBA"]


# ---------- events you add yourself ----------

def test_adding_an_event_raises_its_idea_right_away():
    conn = db.connect(":memory:")
    eid = manual_events.create(conn, name="Glass Hours Night Market", category="Food & Drink", start_date="2099-06-05",
                               end_date="2099-06-06", start_time="17:00", venue="Echo Bay Pier", free=True,
                               url="https://example.org/night-market", notes="saw it on a flyer")
    ev = conn.execute("SELECT * FROM events WHERE id=?", (f"man:{eid}",)).fetchone()
    assert (ev["segment"], ev["source"], ev["genre"], ev["price_min"], ev["end_date"]) == \
        ("Experience", "manual", "Food & Drink", 0.0, "2099-06-06")
    [alert] = conn.execute("SELECT * FROM alerts").fetchall()
    assert alert["kind"] == "experience" and "flyer" not in alert["facts"]  # your notes never reach the model
    import json
    show = json.loads(alert["facts"])["show"]
    assert (show["price"], show["listed_by"], show["time"]) == ("Free", "you (added by hand)", "5:00 PM")
    assert "It's free." in templates.script_for(json.loads(alert["facts"]))
    assert not conn.execute("SELECT 1 FROM changes WHERE kind='new'").fetchone()  # you know about it already


def test_editing_clears_fields_and_removing_dismisses_the_idea():
    conn = db.connect(":memory:")
    eid = manual_events.create(conn, name="Soft Arcade Pop-Up", start_date="2099-06-05", end_date="2099-06-07",
                               venue="Static Hall", price_min=10)
    manual_events.update(conn, eid, end_date=None, price_min=None)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (f"man:{eid}",)).fetchone()
    assert (ev["end_date"], ev["price_min"]) == (None, None)
    manual_events.archive(conn, eid)
    assert conn.execute("SELECT active FROM events WHERE id=?", (f"man:{eid}",)).fetchone()["active"] == 0
    assert conn.execute("SELECT status FROM alerts").fetchone()["status"] == "dismissed"
    assert manual_events.list_events(conn) == []


@pytest.mark.parametrize("fields, message", [
    ({"start_date": "2099-06-05", "venue": "X"}, "Name"),
    ({"name": "A", "start_date": "June 5", "venue": "X"}, "YYYY-MM-DD"),
    ({"name": "A", "start_date": "2099-06-05", "end_date": "2099-06-01", "venue": "X"}, "before"),
    ({"name": "A", "start_date": "2099-06-05", "venue": "X", "url": "javascript:alert(1)"}, "http"),
    ({"name": "A", "start_date": "2099-06-05", "venue": "X", "price_min": -5}, "negative"),
    ({"name": "A", "start_date": "2099-06-05", "venue": "X", "category": "Concert"}, "Category"),
])
def test_bad_input_is_refused(fields, message):
    with pytest.raises(manual_events.ManualEventError, match=message):
        manual_events.create(db.connect(":memory:"), **fields)


def test_manual_events_api(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    db.connect(path).close()
    client = TestClient(api.app)
    r = client.post("/api/manual-events", json={"name": "Nova Vale Fest", "start_date": "2099-07-04",
                                                "venue": "Kilo Moon Park", "category": "Festival"})
    assert r.status_code == 201
    eid = r.json()["id"]
    assert client.get("/api/manual-events").json()["events"][0]["name"] == "Nova Vale Fest"
    assert client.get("/api/events", params={"kind": "local", "days": 30000}).json()[0]["source"] == "manual"
    assert client.post("/api/manual-events", json={"name": "x"}).status_code == 400
    assert client.patch(f"/api/manual-events/{eid}", json={"city": "Camden"}).status_code == 200
    assert client.delete(f"/api/manual-events/{eid}").status_code == 200
    assert client.delete("/api/manual-events/999").status_code == 404


def test_a_running_multi_day_event_stays_listed_until_its_last_day_or_until_its_source_drops_it():
    conn = db.connect(":memory:")
    ev = ex.manual_event({"id": 7, "name": "Echo Bay Market", "start_date": "2026-01-01", "end_date": "2099-01-01",
                          "start_time": None, "url": None, "venue": "Echo Bay", "city": None, "price_min": None,
                          "price_max": None, "category": "Market"})
    _store(conn, [ev])
    today = datetime.now(timezone.utc).date().isoformat()
    assert any(e["id"] == "man:7" for e in upcoming_events(conn, today))  # started long ago, still on
    _store(conn, [tm.normalize(tm_event(event_id="other", date="2099-02-01"))], baseline=False)  # man:7 missing now
    assert conn.execute("SELECT active FROM events WHERE id='man:7'").fetchone()["active"] == 0
    assert not any(e["id"] == "man:7" for e in upcoming_events(conn, today))


def test_a_zero_price_is_free_only_where_a_listing_or_you_said_so():
    assert detectors.fmt_price(0.0, 0.0, free_ok=True) == "Free"
    assert detectors.fmt_price(0.0, 0.0) is None  # a $0-$0 ticket listing is a gap, not a promise
    assert detectors.fmt_price(25.0, 40.0) == "$25–$40"
