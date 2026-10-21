"""Sixers: fetch filtering, game scoring, team-week and big-game alerts, and keeping games out of music formats."""
from datetime import datetime, timezone

import pytest

from tracker import db, detectors, llm, store, templates
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)  # Wednesday


def game(event_id, opponent, date, time="19:30:00", home="Philadelphia 76ers"):
    raw = tm_event(event_id=event_id, name=f"{home} vs. {opponent}", artist=home, date=date, time=time,
                   venue="Xfinity Mobile Arena", genre="Basketball", price=None, support=[opponent])
    raw["classifications"] = [{"segment": {"name": "Sports"}, "genre": {"name": "Basketball"},
                               "subGenre": {"name": "NBA"}}]
    return raw


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    run = store.start_run(c, "ticketmaster")
    raws = [
        game("g1", "Cleveland Cavaliers", "2026-10-22", "19:00:00"),
        game("g2", "Detroit Pistons", "2026-10-25"),
        game("g3", "Boston Celtics", "2026-11-10", "20:00:00"),  # marquee, but outside the 7-day window
        game("g4", "Charlotte Hornets", "2026-11-11"),
    ]
    store.upsert_events(c, run, [tm.normalize(r) for r in raws], baseline=True)
    return c


def test_only_tracked_home_teams_are_kept():
    assert tm.is_tracked_team_event(game("a", "Boston Celtics", "2026-11-10"))
    assert not tm.is_tracked_team_event(game("b", "Temple Owls", "2026-11-10", home="Villanova Wildcats Men's Basketball"))
    assert not tm.is_tracked_team_event(tm_event(name="Concert"))


def test_game_scores_and_facts(conn):
    from tracker.scoring import upcoming_events
    by_id = {e["id"]: e for e in upcoming_events(conn, "2026-10-21")}
    assert by_id["g1"]["score"] == 80.0   # 55 base + 25 marquee (Cavaliers), Thursday
    assert by_id["g2"]["score"] == 60.0   # 55 + 5 weekend
    f = detectors.event_facts(by_id["g1"])
    assert (f["artist"], f["opponent"], f["kind"]) == ("Sixers vs. Cavaliers", "Cleveland Cavaliers", "game")


def test_team_week_alert_lists_this_weeks_home_games(conn):
    [a] = detectors.detect_team_week(conn, NOW.date())
    assert a["key"] == "team_week:Philadelphia 76ers:2026-10-19"
    assert [g["opponent"] for g in a["facts"]["shows"]] == ["Cleveland Cavaliers", "Detroit Pistons"]
    script = templates.script_for(a["facts"])
    assert script.startswith("Sixers home games this week. Thursday, October 22 at 7 PM, the Cleveland Cavaliers.")
    assert "All at Xfinity Mobile Arena." in script
    assert llm.check_facts({"script": script, "caption": templates.caption_for(a["facts"])}, a["facts"]) == []


def test_big_game_only_for_marquee_opponents_within_a_week(conn):
    alerts = detectors.detect_big_games(conn, NOW.date())
    assert [a["facts"]["opponent"] for a in alerts] == ["Cleveland Cavaliers"]
    script = templates.script_for(alerts[0]["facts"])
    assert "The Sixers host the Cleveland Cavaliers Thursday, October 22 at 7 PM at Xfinity Mobile Arena." in script
    assert detectors.detect_big_games(conn, datetime(2026, 11, 5).date())[0]["facts"]["opponent"] == "Boston Celtics"


def test_games_stay_out_of_music_formats(conn):
    run = store.start_run(conn, "ticketmaster")
    store.upsert_events(conn, run, [tm.normalize(game("new", "Miami Heat", "2026-12-01"))], baseline=False)
    assert detectors.detect_new_tours(conn, run, "2026-10-21") == []          # schedule additions aren't tours
    assert detectors.detect_stacked_months(conn, datetime(2026, 11, 1).date()) == []


def test_hook_accepts_team_aliases():
    facts = {"artist": "Sixers", "team": "Philadelphia 76ers"}
    assert "76ers" in templates.hook_names(facts) and "Sixers" in templates.hook_names(facts)
