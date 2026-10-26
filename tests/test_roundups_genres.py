"""P1-10 genres for shows without one, and P1-08 picks / lane weekend cuts. Invented artist names only."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
import responses

from tracker import angles, config, db, detectors, drafts, genres, llm, opportunities, store, templates
from tracker.fetchers import ticketmaster as tm
from tracker.scoring import upcoming_events
from tests.factory import tm_event
from tests.test_pipeline_data import ingest

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)  # a Wednesday; the weekend is Oct 23-25
TODAY = "2026-10-21"


@pytest.fixture
def conn():
    return db.connect(":memory:")


def cal_event(name, date="2026-10-24", venue="Small Room", event_id=None):
    """A venue-calendar show as venue_calendars.normalize() returns it: no genre, a name-based attraction."""
    return {"id": event_id or f"cal:sr:{name.lower().replace(' ', '-')}-{date}", "name": name, "url": "u", "image_url": None,
            "local_date": date, "local_time": "20:00:00", "status": "onsale", "segment": "Music", "genre": None,
            "subgenre": None, "price_min": 20.0, "price_max": None, "currency": "USD", "onsale_start": None,
            "onsale_end": None, "venue": {"id": "cal:sr", "name": venue, "city": "Philadelphia", "state": "PA"},
            "attractions": [{"id": "name:" + name.lower().replace(" ", "-"), "name": name, "genre": None,
                             "subgenre": None, "tm_upcoming_total": None}], "presales": []}


def add_cal(conn, *events):
    run = store.start_run(conn, "tm")
    store.upsert_events(conn, run, list(events), baseline=True)


def by_id(conn):
    return {e["id"]: e for e in upcoming_events(conn, TODAY)}


# ---------- P1-10: genres ----------

def test_listing_genre_wins_and_placeholders_are_never_a_genre(conn):
    raw = tm_event(event_id="u", name="Nova Vale", genre="Pop")
    raw["classifications"][0]["genre"]["name"] = "Undefined"  # the event says nothing, the artist says Pop
    ingest(conn, [raw, tm_event(event_id="l", name="Kilo Moon", genre="R&B")])
    e = by_id(conn)
    assert (e["u"]["genre"], e["u"]["genre_source"]) == ("Pop", "artist")
    assert (e["l"]["genre"], e["l"]["genre_source"]) == ("R&B", "listing")
    conn.execute("UPDATE attractions SET genre='Other'")
    assert by_id(conn)["u"]["genre"] is None and by_id(conn)["u"]["genre_source"] is None


class FakeTM:
    def __init__(self, answers):
        self.answers, self.asked, self.calls = answers, [], 0

    def find_attraction(self, name):
        self.asked.append(name)
        self.calls += 1
        status, genre = self.answers.get(name, ("none", None))
        if status != "found":
            return status, None
        return "found", {"name": name, "classifications": [{"genre": {"name": genre}, "subGenre": {"name": "Undefined"}}]}


def test_calendar_acts_get_ticketmaster_genre_by_exact_unambiguous_name(conn):
    # dates relative to today: lookup_candidates only considers upcoming shows (fixed dates expired 2026-10-25)
    D1, D2 = ((date.today() + timedelta(days=n)).isoformat() for n in (30, 36))
    listing = [cal_event("Nova Vale", date=D1), cal_event("Twin Name", date=D1), cal_event("Open Mic Night", date=D1), cal_event("EQ", date=D1),
               cal_event("Kilo Moon", date=D2)]
    add_cal(conn, *listing)
    client = FakeTM({"Nova Vale": ("found", "Hip-Hop/Rap"), "Twin Name": ("ambiguous", None),
                     "Kilo Moon": ("found", "Undefined")})
    counts = genres.fill_from_ticketmaster(conn, client, limit=10)
    assert counts == {"found": 1, "none": 0, "ambiguous": 1, "no_genre": 1}
    assert "Open Mic Night" not in client.asked and "EQ" not in client.asked  # event titles, too-short names
    assert client.asked[0] == "Nova Vale"  # soonest show first (D1 before D2)
    e = by_id(conn)
    nova = e[f"cal:sr:nova-vale-{D1}"]
    assert (nova["genre"], nova["subgenre"], nova["genre_source"]) == ("Hip-Hop/Rap", None, "tm_search")
    assert nova["niche"]  # an inferred lane genre counts toward your lane
    assert e[f"cal:sr:twin-name-{D1}"]["genre"] is None
    # cached: nothing is asked again until GENRE_RECHECK_DAYS pass, and a re-ingest keeps the found genre
    assert genres.fill_from_ticketmaster(conn, client, limit=10) == {"found": 0, "none": 0, "ambiguous": 0, "no_genre": 0}
    add_cal(conn, *listing)  # the next run reads the calendar again (still no genre in the listing)
    assert by_id(conn)[f"cal:sr:nova-vale-{D1}"]["genre"] == "Hip-Hop/Rap"
    old = (datetime.now(timezone.utc) - timedelta(days=config.GENRE_RECHECK_DAYS + 1)).isoformat()
    conn.execute("UPDATE attractions SET genre_checked_at=?", (old,))  # a month later: unknowns are retried
    assert {c["name"] for c in genres.lookup_candidates(conn, 10)} == {"Twin Name", "Kilo Moon"}


def test_lookup_failures_never_raise(conn):
    add_cal(conn, cal_event("Nova Vale"), cal_event("Kilo Moon"))

    class Broken(FakeTM):
        def find_attraction(self, name):
            raise tm.BudgetExceeded("budget")
    assert genres.fill_from_ticketmaster(conn, Broken({}), limit=10)["found"] == 0
    assert conn.execute("SELECT COUNT(*) FROM attractions WHERE genre_checked_at IS NOT NULL").fetchone()[0] == 0


@responses.activate
def test_find_attraction_needs_exactly_one_exact_name():
    def reply(*names):
        return {"_embedded": {"attractions": [{"name": n, "classifications": [{"genre": {"name": "Rock"}}]} for n in names]}}
    responses.get(tm.ATTRACTIONS_URL, json=reply("Nova Vale", "Nova Vale Tribute"))
    responses.get(tm.ATTRACTIONS_URL, json=reply("Nova Vale", "nova  vale"))
    responses.get(tm.ATTRACTIONS_URL, json=reply("Nova Valerie"))
    client = tm.TicketmasterClient(api_key="test", min_interval=0)
    status, a = client.find_attraction("Nova Vale")
    assert status == "found" and tm.attraction_genre(a) == ("Rock", None)
    assert client.find_attraction("Nova Vale")[0] == "ambiguous"  # two acts with this name
    assert client.find_attraction("Nova Vale")[0] == "none"       # a prefix isn't a match
    assert responses.calls[0].request.params["classificationName"] == "music"


def test_api_genres_include_filled_ones(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tracker import api
    path = tmp_path / "g.db"
    c = db.connect(path)
    add_cal(c, cal_event("Nova Vale", date=(datetime.now().date() + timedelta(days=3)).isoformat()))
    genres.fill_from_ticketmaster(c, FakeTM({"Nova Vale": ("found", "Pop")}), limit=5)
    c.close()
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    client = TestClient(api.app)
    assert client.get("/api/genres").json() == ["Pop"]
    [ev] = client.get("/api/events", params={"genre": "Pop"}).json()
    assert ev["genre_source"] == "tm_search"


# ---------- P1-08: picks and lane cuts ----------

def weekend_raws():
    """Fri-Sun shows: 3 rock (big), 4 in the lane (hip-hop / R&B), 2 small jazz, plus one next week."""
    raws = [tm_event(event_id=f"r{i}", name=f"Rock Band {i}", date="2026-10-24", genre="Rock",
                     venue="Xfinity Mobile Arena", upcoming=200) for i in range(3)]
    raws += [tm_event(event_id=f"h{i}", name=f"Rapper {i}", date="2026-10-23", genre="Hip-Hop/Rap", upcoming=20 + i)
             for i in range(3)]
    raws += [tm_event(event_id="rb", name="Singer RB", date="2026-10-25", genre="R&B", upcoming=10),
             tm_event(event_id="nw", name="Next Week", date="2026-10-31", genre="Pop")]
    raws += [tm_event(event_id=f"j{i}", name=f"Jazz Trio {i}", date="2026-10-25", genre="Jazz", upcoming=1)
             for i in range(2)]
    return raws


def test_weekend_and_stacked_month_get_ranked_picks(conn):
    ingest(conn, weekend_raws())
    [weekend] = detectors.detect_weekend(conn, NOW.date())
    f = weekend["facts"]
    assert f["pick_count"] == len(f["picks"]) == config.PICKS_MAX
    ranked = sorted(upcoming_events(conn, TODAY), key=lambda e: -e["score"])
    in_weekend = {s["artist"] for s in f["shows"]}
    assert [p["artist"] for p in f["picks"]] == [e["headliner"] for e in ranked if e["headliner"] in in_weekend][:5]
    assert {p["artist"] for p in f["picks"]} <= in_weekend

    ingest(conn, [tm_event(event_id=f"c{i}", name=f"Country {i}", genre="Country", date=f"2026-11-{10 + i}",
                           venue="The Met Philadelphia", upcoming=50 + i) for i in range(6)])
    [month] = detectors.detect_stacked_months(conn, NOW.date())
    assert month["facts"]["picks"][0]["artist"] == "Country 5"  # biggest tour = highest score
    assert month["facts"]["pick_count"] == 5


def test_too_few_shows_means_no_picks():
    assert detectors.picks_facts([]) == {}


def test_one_pick_per_artist(conn):
    raws = [tm_event(event_id=f"n{i}", name="Nova Vale", date=f"2026-10-2{3 + i}", upcoming=300) for i in range(2)]
    raws += [tm_event(event_id=f"o{i}", name=f"Other {i}", date="2026-10-24", upcoming=5 + i) for i in range(5)]
    ingest(conn, raws)
    picks = detectors.picks_facts(upcoming_events(conn, TODAY))["picks"]
    assert [p["artist"] for p in picks] == ["Nova Vale", "Other 4", "Other 3", "Other 2", "Other 1"]
    assert picks[0]["date"] == "Fri, Oct 23"  # same score both nights: the earlier one


@pytest.mark.parametrize("shows, label", [
    ([("Hip-Hop/Rap", None), ("R&B", None), ("Hip-Hop/Rap", None)], "Hip-Hop & R&B"),
    ([("Alternative", "Indie Pop"), ("Dance/Electronic", None), ("Pop", None), ("Hip-Hop/Rap", "Trap")],
     "Pop, EDM & Hip-Hop"),
    ([("Pop", None)], "Pop"),
])
def test_lane_label_names_every_lane_genre(shows, label):
    assert detectors.lane_label([{"genre": g, "subgenre": s} for g, s in shows]) == label


def test_long_lane_label_keeps_the_picks_hook_short():
    facts = {**LANE_FACTS, "lane": "Pop, EDM, Hip-Hop & R&B extra words here"}
    hook = angles.find(facts, "picks")["hook"]
    assert hook == "My 3 picks for Philly this weekend"


def test_lane_weekend_cut(conn):
    ingest(conn, weekend_raws())
    alerts = detectors.run_all(conn, 0, NOW)
    [lane] = [a for a in alerts if a["key"] == "weekend_lane:2026-10-23"]
    f = lane["facts"]
    assert f["lane"] == "Hip-Hop & R&B" and len(f["shows"]) == 4
    assert {s["genre"] for s in f["shows"]} == {"Hip-Hop/Rap", "R&B"}
    assert lane["title"].startswith("Your lane this weekend: Hip-Hop & R&B (4 shows")
    assert "picks" not in f  # 4 shows: the picks would be the whole list again (same video as "The list")


def test_lane_cut_skipped_when_the_general_roundup_already_covers_it(conn):
    raws = [tm_event(event_id=f"h{i}", name=f"Rapper {i}", date="2026-10-24", genre="Hip-Hop/Rap") for i in range(2)]
    raws += [tm_event(event_id="rb", name="Singer RB", date="2026-10-24", genre="R&B"),
             tm_event(event_id="pp", name="Pop Star", date="2026-10-25", genre="Pop")]
    ingest(conn, raws)  # only lane shows this weekend: the general roundup is the same video
    alerts = detectors.run_all(conn, 0, NOW)
    assert [a["key"] for a in alerts if a["kind"] == "weekend"] == ["weekend:2026-10-23"]


def test_lane_cut_needs_three_lane_shows_and_ignores_unknown_genres(conn):
    raws = [tm_event(event_id=f"r{i}", name=f"Rock Band {i}", date="2026-10-24", genre="Rock") for i in range(4)]
    raws += [tm_event(event_id=f"h{i}", name=f"Rapper {i}", date="2026-10-23", genre="Hip-Hop/Rap") for i in range(2)]
    ingest(conn, raws)
    add_cal(conn, cal_event("Mystery Act"))  # no genre: never counted toward a lane or genre roundup
    assert detectors.detect_lane_weekend(conn, NOW.date(), []) == []


def test_unknown_genres_never_make_a_stacked_month(conn):
    add_cal(conn, *[cal_event(f"Act {i}", date=f"2026-11-{10 + i}") for i in range(8)])
    conn.execute("UPDATE attractions SET genre='Other', popularity=100")
    assert detectors.detect_stacked_months(conn, NOW.date()) == []


def test_picks_backfill_doesnt_mark_drafts_out_of_date(conn):
    """Alerts raised before picks existed get them quietly: no history row, no 'facts changed' on drafts."""
    ingest(conn, weekend_raws())
    [weekend] = detectors.detect_weekend(conn, NOW.date())
    old = {**weekend, "facts": {k: v for k, v in weekend["facts"].items() if k not in detectors.DERIVED_FACTS}}
    detectors.save_alerts(conn, [old])
    detectors.save_alerts(conn, [weekend])
    row = conn.execute("SELECT facts, updated_at FROM alerts").fetchone()
    assert json.loads(row["facts"])["pick_count"] == 5 and row["updated_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM alert_history").fetchone()[0] == 0
    assert detectors.facts_diff(old["facts"], weekend["facts"]) == []


# ---------- angles + drafting ----------

LANE_FACTS = {"format": "weekend", "weekend": "Fri, Oct 23 – Sun, Oct 25", "lane": "Hip-Hop & R&B",
              "shows": [{"artist": a, "date": "Sat, Oct 24", "venue": "Union Transfer", "url": "u"}
                        for a in ("Nova Vale", "Kilo Moon", "Rapper Two", "Singer Four")]}
LANE_FACTS = {**LANE_FACTS, "picks": LANE_FACTS["shows"][:3], "pick_count": 3}
MONTH_FACTS = {"format": "stacked_month", "month": "November 2026", "genre": "R&B", "notable_show_count": 7,
               "shows": LANE_FACTS["shows"], "picks": LANE_FACTS["shows"][:3], "pick_count": 3}


@pytest.mark.parametrize("facts", [LANE_FACTS, MONTH_FACTS, {**LANE_FACTS, "lane": None}])
def test_picks_angle_is_fact_clean(facts):
    a = angles.find(facts, "picks")
    assert a and llm.check_facts({"hook": a["hook"]}, facts) == []
    assert len(a["hook"].split()) <= templates.MAX_HOOK_WORDS
    for other in angles.angles_for(facts):
        assert llm.check_facts({"hook": other["hook"]}, facts) == [], other["hook"]
    assert "picks" not in [x["key"] for x in angles.angles_for({k: v for k, v in facts.items() if k not in ("picks",)})]


def test_picks_hooks_read_well():
    assert angles.find(LANE_FACTS, "picks")["hook"] == "My 3 Hip-Hop & R&B picks for Philly this weekend"
    assert angles.find(MONTH_FACTS, "picks")["hook"] == "My 3 R&B picks for Philly in November"
    assert angles.find(LANE_FACTS, "news")["hook"] == "4 Hip-Hop & R&B shows in Philly this weekend"


def test_drafting_from_picks_keeps_to_the_picks(conn, monkeypatch):
    detectors.save_alerts(conn, [{"key": "w", "kind": "weekend", "title": "t", "score": 60, "facts": LANE_FACTS}])
    seen = {}

    def fake_generate(facts, model=None, script=None, caption=None, angle=None, conn=None):
        seen["facts"], seen["prompt"] = facts, llm.build_prompt(facts)
        return {"hook": angle["hook"], "script": "Nova Vale, Kilo Moon and Singer Four.", "caption": "Save this.",
                "hashtags": ["#philly"], "model": "fake"}
    monkeypatch.setattr(llm, "generate", fake_generate)
    d = conn.execute("SELECT * FROM drafts WHERE id=?", (drafts.draft_alert(conn, 1, angle="picks"),)).fetchone()
    assert [s["artist"] for s in seen["facts"]["shows"]] == ["Nova Vale", "Kilo Moon", "Rapper Two"]
    assert seen["facts"]["picked_from"] == 4 and "Mention no other shows" in seen["prompt"]
    assert "Singer Four" not in seen["prompt"]
    assert json.loads(d["onscreen"])[:2] == ["MY 3 PICKS", "HIP-HOP & R&B · PHILLY THIS WEEKEND · Fri, Oct 23 – Sun, Oct 25"]
    assert len(json.loads(d["onscreen"])) == 2 + 3
    assert json.loads(d["facts"]) == LANE_FACTS  # the alert's full facts, for "facts changed" later
    assert d["angle"] == "picks"


def test_prompt_never_sends_picks_twice():
    assert "picks" not in llm._prompt_facts(LANE_FACTS) and "pick_count" not in llm._prompt_facts(LANE_FACTS)


def test_lane_cut_scores_as_your_lane():
    alert = {"id": 1, "kind": "weekend", "score": 60, "created_at": NOW.isoformat(), "status": "new",
             "post_by": (NOW + timedelta(days=2)).isoformat(), "expires_at": (NOW + timedelta(days=3)).isoformat(),
             "event_ids": None, "shortlisted": 0, "snoozed_until": None, "facts": LANE_FACTS}
    ev = [{"id": "e1", "headliner": "Nova Vale", "popularity": 50, "venue_tier": 50, "venue_name": "Union Transfer",
           "my_plays": 0, "niche": True, "genre": "Hip-Hop/Rap"}]
    out = opportunities.evaluate(alert, ev, None, [], [], NOW)
    comps = {c["key"]: c for c in out["components"]}
    assert comps["story"]["value"] == 45 and "your lane" in comps["story"]["why"]
    assert comps["local"]["value"] == 100


def test_picks_skip_games_and_never_repeat_the_whole_list(conn):
    raws = [tm_event(event_id=f"m{i}", name=f"Band {i}", date="2026-10-24", upcoming=10 + i) for i in range(4)]
    ingest(conn, raws)
    events = upcoming_events(conn, TODAY)
    assert detectors.picks_facts(events) == {}  # 4 shows -> 4 picks = the list itself
    game = {**events[0], "id": "g", "headliner": "Sixers vs. Bucks", "kind": "game", "segment": "Sports",
            "genre": "Basketball", "score": 999}
    picks = detectors.picks_facts(events + [game])
    assert picks and "Sixers vs. Bucks" not in [p["artist"] for p in picks["picks"]]


def test_lane_cut_counts_a_two_night_run_once(conn):
    raws = [tm_event(event_id=f"n{i}", name="Nova Vale", date=f"2026-10-2{3 + i}", genre="Pop", upcoming=50)
            for i in range(2)]
    raws += [tm_event(event_id=f"h{i}", name=f"Rapper {i}", date="2026-10-24", genre="Hip-Hop/Rap") for i in range(2)]
    raws += [tm_event(event_id=f"r{i}", name=f"Rock Band {i}", date="2026-10-24", genre="Rock", upcoming=200)
             for i in range(3)]
    ingest(conn, raws)
    [lane] = detectors.detect_lane_weekend(conn, NOW.date(), [])
    assert [s["artist"] for s in lane["facts"]["shows"]].count("Nova Vale") == 1 and "(3 shows)" in lane["title"]
