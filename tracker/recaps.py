"""Recaps: lots of broad roundup ideas, each one slice of what's on in Philly, for the Recaps tab.

The main detectors (detectors.py) raise the few roundups that matter most (the weekend roundup, a stacked month,
your lane this weekend). This module raises many more, cheaply and from the same data: every recap type below
crossed with every time window that applies. Examples: "Hip-Hop in Philly the week of Nov 30", "Underrated shows
in December", "Dec 1-15 in Philly", "Gracie Abrams: 2 nights in Philly", "After the Latto show: late-night
shows the same night", "Christmas week in Philly".

Every recap is an alert of kind "recap" with facts format "recap". Its facts are only shows (detectors.event_facts)
plus words that describe the slice (`theme`, `when`): the draft states nothing else. A recap is a label on a list
of shows, never a claim about them: "underrated" means smaller artists in smaller rooms by our own numbers, and the
guide (guide()) tells the model not to dress it up. Fan counts and your listening history pick shows; they're
never in the facts.

Recaps aren't drafted automatically (drafts.draft_pending skips them): there are too many, so you pick. The ones
in your lane with the best opportunity score also show on "What to post" (opportunities.queue).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .detectors import (LANE_NAMES, et_iso, event_facts, fmt_date, fmt_utc_local, history_facts, picks_facts, ranked,
                        upcoming_weekend)
from .scoring import is_comedy, is_experience, is_game, is_music, upcoming_events
from .store import now_iso

KIND = "recap"
TZ = ZoneInfo(config.TIMEZONE)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]

# What each recap type is called on the dashboard (its filter and pill)
TYPE_LABEL = {
    "genre": "Genre", "scene": "Scene (subgenre)", "overview": "What's on", "underrated": "Underrated",
    "cheap": "Under $25", "venue": "Venue", "city": "Outside the city", "late_night": "Late night",
    "weeknight": "Weeknights", "big_rooms": "Biggest shows", "small_rooms": "Big names, small rooms",
    "comeback": "Back after years", "lineups": "Stacked lineups", "comedy": "Comedy", "sports": "Sports",
    "local": "Markets & festivals", "holiday_shows": "Holiday shows", "multi_night": "Multiple nights",
    "afterparty": "After the show", "just_announced": "Just announced", "on_sale": "On sale this week",
}

# How good a video each type usually makes, 0-100 (the opportunity score's "story"), with the card's reason
STORY = {
    "multi_night": (70, "One artist, several Philly nights"), "afterparty": (65, "What to do after a big show"),
    "just_announced": (70, "Fresh announcements, all in one video"), "comeback": (75, "Artists back after years"),
    "small_rooms": (70, "Big names in small rooms"), "underrated": (60, "Under-the-radar picks"),
    "genre": (55, "One genre, one stretch of dates"), "scene": (55, "A scene's shows, for its fans"),
    "overview": (50, "What's on, for anyone"), "cheap": (60, "Cheap nights out"), "holiday_shows": (65, "Seasonal"),
    "big_rooms": (55, "The biggest shows"), "lineups": (50, "Several acts for one ticket"),
    "late_night": (45, "Late-night shows"), "weeknight": (45, "Weeknight plans"), "venue": (45, "One venue's run"),
    "city": (40, "Worth the drive"), "comedy": (50, "Stand-up roundup"), "sports": (40, "Every home game"),
    "local": (60, "Not a concert: a wider audience"), "on_sale": (55, "Tickets to set an alarm for"),
}

# Per type: what the video is, for the model (llm.build_prompt). Facts carry the shows; this carries the framing.
GUIDES = {
    "genre": "Only {theme}: one line per show with artist, day and venue.",
    "scene": "For fans of {theme}: one line per show with artist, day and venue.",
    "overview": "A quick tour of what's on in Philly {when}, one line per show, in date order.",
    "underrated": "Smaller artists in smaller rooms, picked because they're less known. Call them underrated or under "
                  "the radar; don't claim they're rising, viral, about to blow up or selling fast.",
    "cheap": "Shows whose cheapest listed ticket is {price_cap}: say each one's price exactly as written.",
    "venue": "Everything at {venue} {when}: one line per show with artist and day.",
    "city": "Shows in {city} {when}, outside Philly proper: one line per show with artist, day and venue.",
    "late_night": "Shows that start late (each one's time is in the facts): name the start times.",
    "weeknight": "Monday-to-Thursday shows: plans for a weeknight. One line per show.",
    "big_rooms": "The biggest shows in the biggest rooms {when}. Don't describe crowds or production.",
    "small_rooms": "Artists who could fill a bigger room, playing a small one. Don't state fan counts or sales.",
    "comeback": "Artists back in Philly after a long gap: each show's history says when they last played here "
                "(from setlist.fm). Use only those years; never say 'first time ever'.",
    "lineups": "Shows with several acts on the bill: name the support acts given in the facts.",
    "comedy": "Stand-up shows: say show, never concert.",
    "sports": "Every Philly home game: matchup, day and time.",
    "local": "Markets, festivals and seasonal events: what, when, where. Don't describe what's there.",
    "holiday_shows": "Holiday-season shows. Don't invent traditions, guests or set lists.",
    "multi_night": "{artist} has {night_count} Philly dates: list every date and venue. Don't say why, and don't "
                   "call any night sold out or nearly sold out.",
    "afterparty": "Plans for after {headliner} on {night}: shows that start late that same night. Only call one an "
                  "official afterparty if its name says so. Don't say when the concert ends or that people will "
                  "come from it.",
    "just_announced": "Shows announced for Philly in the last week, one line each: artist, date, venue.",
    "on_sale": "Ticket sales opening soon: for each, what opens and when (copy it exactly).",
}

GENRE_MIN = 3          # acts for a genre or scene recap
LIST_MAX = 10          # shows listed in one recap (the rest stay in the dashboard's "add a show")
UNDERRATED_FANS = (1_000, 60_000)   # Deezer fans: heard of, but small
UNDERRATED_MAX_TIER = 40
SMALL_ROOM_FANS = 100_000
SMALL_ROOM_MAX_TIER = 30
BIG_ROOM_MIN_TIER = 60
CHEAP_MAX = 25
LATE_START = "21:30"
MULTI_NIGHT_DAYS = 120  # how far ahead a run of dates is worth a video
RUN_GAP_DAYS = 7        # dates further apart than this aren't one run
AFTER_DAYS = 21
AFTER_MIN_SCORE = 55
HOLIDAY_WORDS = re.compile(r"christmas|holiday|nutcracker|jingle|santa|hanukkah|chanukah|kwanzaa|xmas|noel|"
                           r"new year|nye\b|carol|mistletoe|winter wonder", re.I)
AFTERPARTY = re.compile(r"after\s*-?\s*party", re.I)


@dataclass
class Window:
    key: str           # stable part of the alert key, e.g. "week:2026-11-30"
    size: str          # weekend, week, month, half, holiday
    start: date
    end: date
    when: str          # how the video says it: "the week of Nov 30", "December", "Christmas week (Dec 21-27)"
    post_by: str
    expires: str


# ---------- windows ----------

def _span(a: date, b: date) -> str:
    """'Dec 4–6', 'Nov 30 – Dec 6'."""
    if a == b:
        return f"{a:%b} {a.day}"
    if a.month == b.month:
        return f"{a:%b} {a.day}–{b.day}"
    return f"{a:%b} {a.day} – {b:%b} {b.day}"


def _month_name(d: date, today: date) -> str:
    return MONTHS[d.month - 1] + ("" if d.year == today.year else f" {d.year}")


def _month_end(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = _month_end(date(year, month, 1))
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _holidays(year: int) -> list[tuple[str, str, date, date]]:
    """(key, name, first day, last day) of the stretches people make plans around."""
    thanks = _nth_weekday(year, 11, 3, 4)
    xmas = date(year, 12, 25)
    memorial, labor = _last_weekday(year, 5, 0), _nth_weekday(year, 9, 0, 1)
    vday = date(year, 2, 14)
    vfri = vday - timedelta(days=(vday.weekday() - 4) % 7)
    halloween = date(year, 10, 31)
    hfri = halloween - timedelta(days=(halloween.weekday() - 4) % 7)
    mlk = _nth_weekday(year, 1, 0, 3)
    pats = date(year, 3, 17)
    pfri = pats - timedelta(days=(pats.weekday() - 4) % 7)
    return [
        ("thanksgiving_week", "Thanksgiving week", thanks - timedelta(days=3), thanks + timedelta(days=3)),
        ("thanksgiving_weekend", "Thanksgiving weekend", thanks + timedelta(days=1), thanks + timedelta(days=3)),
        ("christmas_week", "Christmas week", xmas - timedelta(days=xmas.weekday()),
         xmas - timedelta(days=xmas.weekday()) + timedelta(days=6)),
        ("nye", "New Year's Eve", date(year, 12, 31), date(year, 12, 31)),
        ("new_years_weekend", "New Year's weekend", date(year, 12, 30), date(year + 1, 1, 1)),
        ("mlk_weekend", "MLK weekend", mlk - timedelta(days=2), mlk),
        ("valentines_weekend", "Valentine's weekend", vfri, vfri + timedelta(days=2)),
        ("st_patricks_weekend", "St. Patrick's weekend", pfri, pfri + timedelta(days=2)),
        ("memorial_day_weekend", "Memorial Day weekend", memorial - timedelta(days=2), memorial),
        ("july_4th", "Fourth of July week", date(year, 7, 1), date(year, 7, 7)),
        ("labor_day_weekend", "Labor Day weekend", labor - timedelta(days=2), labor),
        ("halloween_weekend", "Halloween weekend", hfri, hfri + timedelta(days=2)),
    ]


def windows(today: date) -> list[Window]:
    """The stretches of dates recaps cover, from today: this and next weekend, this week (early in it) and next,
    this month (early in it), next month and its halves, and holidays in the next 60 days."""
    out = []

    def add(key, size, start, end, when, post_by, expires):
        if end >= today:
            out.append(Window(key, size, max(start, today), end, when, post_by, expires))

    horizon = today + timedelta(days=60)  # holidays first: a plain week with the same dates is dropped below
    for year in (today.year, today.year + 1):
        for key, name, a, b in _holidays(year):
            if a <= horizon:
                when = name if a == b else f"{name} ({_span(a, b)})"
                add(f"{key}:{a.year}", "holiday", a, b, when, et_iso(a - timedelta(days=4), 12), et_iso(a, 18))
    fri, _ = upcoming_weekend(today)
    for f in (fri, fri + timedelta(days=7)):
        sun = f + timedelta(days=2)
        add(f"weekend:{f}", "weekend", f, sun, f"the weekend of {_span(f, sun)}", et_iso(f, 12), et_iso(f + timedelta(days=1), 18))
    monday = today - timedelta(days=today.weekday())
    weeks = ([monday] if today.weekday() <= 2 else []) + [monday + timedelta(days=7)]
    for m in weeks:
        add(f"week:{m}", "week", m, m + timedelta(days=6), f"the week of {m:%b} {m.day}", et_iso(m, 12),
            et_iso(m + timedelta(days=3), 18))
    first = today.replace(day=1)
    nxt = _month_end(today) + timedelta(days=1)
    months = ([first] if today.day <= 10 else []) + [nxt]
    for m in months:
        end = _month_end(m)
        add(f"month:{m:%Y-%m}", "month", m, end, f"in {_month_name(m, today)}", et_iso(m, 0), et_iso(m.replace(day=15), 23, 59))
    for m in (first, nxt):  # halves that haven't started ("December 1-15" on Dec 9 is a stale title)
        name, mid, end = _month_name(m, today), m.replace(day=15), _month_end(m)
        if m >= today:
            add(f"half1:{m:%Y-%m}", "half", m, mid, f"{name.split()[0]} 1–15", et_iso(m - timedelta(days=3), 12),
                et_iso(m + timedelta(days=7), 23, 59))
        if mid + timedelta(days=1) >= today:
            add(f"half2:{m:%Y-%m}", "half", mid + timedelta(days=1), end, f"{name.split()[0]} 16–{end.day}",
                et_iso(mid - timedelta(days=2), 12), et_iso(mid + timedelta(days=8), 23, 59))
    seen, unique = set(), []
    for w in out:  # the same dates twice (Thanksgiving week is also "the week of Nov 23"): keep the first
        if (w.start, w.end) not in seen:
            seen.add((w.start, w.end))
            unique.append(w)
    return unique


# ---------- picking shows ----------

def _usable(e: dict) -> bool:
    return e["status"] not in ("cancelled", "postponed")


def _in(e: dict, w: Window) -> bool:
    return w.start.isoformat() <= e["local_date"] <= w.end.isoformat()


def _one_per_act(events: list[dict], keep_order: bool = False) -> list[dict]:
    """Best first (or as given), one entry per headliner (a two-night run is one line, at its best night)."""
    seen: set = set()
    return [e for e in (events if keep_order else ranked(events))
            if not (e["headliner"] in seen or seen.add(e["headliner"]))]


def _genre_name(e: dict) -> str | None:
    g = e.get("genre")
    if not g or g in config.NON_GENRES:
        return None
    return LANE_NAMES.get(g, g)


def _music(events, w):
    return [e for e in events if is_music(e) and _in(e, w)]


def _recap(rtype: str, key: str, title: str, theme: str, w: Window | None, shows: list[dict], *,
           when: str | None = None, extra: dict | None = None, post_by: str | None = None,
           expires: str | None = None, min_shows: int = 3, picks: bool = True,
           keep_order: bool = False) -> dict | None:
    """One recap alert from its best shows (or the first ones, with keep_order), listed in date order. None if
    there are too few."""
    best = _one_per_act(shows, keep_order)[:LIST_MAX] if rtype != "multi_night" else shows
    if len(best) < min_shows:
        return None
    listed = sorted(best, key=lambda e: (e["local_date"], e["local_time"] or ""))
    when = when or (w.when if w else "")
    facts = {"format": "recap", "recap_type": rtype, "theme": theme, "when": when,
             "show_count": len(listed), "shows": [event_facts(e) for e in listed], **(extra or {})}
    if picks:
        facts.update(picks_facts(best))
    return {
        "key": f"recap:{rtype}:{key}",
        "kind": KIND,
        "title": title,
        "score": round(sum(e["score"] for e in best[:5]) / min(len(best), 5), 1),
        "facts": facts,
        "event_ids": [e["id"] for e in listed],
        "post_by": post_by or (w.post_by if w else None),
        "expires_at": expires or (w.expires if w else None),
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _by_window(events: list[dict], wins: list[Window], today: date) -> list[dict]:
    """Every type that's one slice of one window."""
    out = []
    for w in wins:
        music = [e for e in _music(events, w) if _usable(e)]
        short = w.size in ("weekend", "week", "holiday")
        # Genre (lane names: "Hip-Hop" for Hip-Hop/Rap) and, over a month, the subgenre scenes inside them
        by_genre = defaultdict(list)
        for e in music:
            if _genre_name(e):
                by_genre[_genre_name(e)].append(e)
        for g, evs in by_genre.items():
            n = len(_one_per_act(evs)[:LIST_MAX])
            out.append(_recap("genre", f"{w.key}:{g}", f"{g} in Philly {w.when} ({_plural(n, 'show')})",
                              f"{g} shows", w, evs, extra={"genre": g}, min_shows=GENRE_MIN))
        if w.size in ("month", "half"):
            by_sub = defaultdict(list)
            for e in music:
                s = e.get("subgenre")
                if s and s not in config.NON_GENRES and s != e.get("genre") and LANE_NAMES.get(s, s) != _genre_name(e):
                    by_sub[s].append(e)
            for s, evs in by_sub.items():
                out.append(_recap("scene", f"{w.key}:{s}", f"{s} in Philly {w.when}", f"{s} shows", w, evs,
                                  extra={"genre": s}, min_shows=GENRE_MIN))
        # What's on, overall: the week ahead, half-months and holidays (weekends have the main roundup)
        if w.size in ("week", "half", "holiday"):
            mixed = [e for e in events if _in(e, w) and _usable(e) and (is_music(e) or is_comedy(e) or is_experience(e))]
            out.append(_recap("overview", w.key, f"What's on in Philly: {w.when}", "what's on in Philly", w, mixed,
                              min_shows=4))
        # Underrated: heard of, but small, in a smaller room
        lo, hi = UNDERRATED_FANS
        under = [e for e in music if e.get("fans") and lo <= e["fans"] <= hi and (e.get("venue_tier") or 0) <= UNDERRATED_MAX_TIER]
        under.sort(key=lambda e: (-(e.get("niche") or 0), -e["fans"]))  # your lane first, then the best-known
        out.append(_recap("underrated", w.key, f"Underrated shows in Philly {w.when}", "underrated shows", w,
                          under, min_shows=4, picks=False, keep_order=True))
        cheap = [e for e in events if _in(e, w) and _usable(e) and (is_music(e) or is_comedy(e))
                 and e.get("price_min") and 0 < e["price_min"] <= CHEAP_MAX]
        out.append(_recap("cheap", w.key, f"Shows under ${CHEAP_MAX} in Philly {w.when}", f"shows under ${CHEAP_MAX}",
                          w, cheap, extra={"price_cap": f"under ${CHEAP_MAX}"}, min_shows=4))
        if w.size in ("weekend", "week", "holiday"):
            late = [e for e in music if (e.get("local_time") or "") >= LATE_START]
            out.append(_recap("late_night", w.key, f"Late-night shows in Philly {w.when}", "late-night shows", w, late))
            comedy = [e for e in events if is_comedy(e) and _in(e, w) and _usable(e)]
            out.append(_recap("comedy", w.key, f"Stand-up comedy in Philly {w.when}", "stand-up comedy", w, comedy))
        if w.size == "week":
            weeknights = [e for e in music if date.fromisoformat(e["local_date"]).weekday() <= 3]
            out.append(_recap("weeknight", w.key, f"Weeknight shows in Philly {w.when}", "weeknight shows", w,
                              weeknights, min_shows=4))
            games = [e for e in events if is_game(e) and _in(e, w) and _usable(e)]
            out.append(_recap("sports", w.key, f"Every Philly home game {w.when}", "Philly home games", w, games,
                              picks=False))
        if w.size == "month":
            month = w.when.removeprefix("in ")
            by_venue, by_city = defaultdict(list), defaultdict(list)
            for e in music:
                by_venue[e["venue_name"]].append(e)
                if e.get("venue_city") and e["venue_city"] != "Philadelphia":
                    by_city[e["venue_city"]].append(e)
            for v, evs in by_venue.items():
                if v:
                    out.append(_recap("venue", f"{w.key}:{v}", f"{month} at {v} ({_plural(len(_one_per_act(evs)), 'show')})",
                                      f"shows at {v}", w, evs, extra={"venue": v}, min_shows=4))
            for c, evs in by_city.items():
                out.append(_recap("city", f"{w.key}:{c}", f"Worth the drive: {c} {w.when}", f"shows in {c}", w, evs,
                                  extra={"city": c}, min_shows=4))
            big = [e for e in music if (e.get("venue_tier") or 0) >= BIG_ROOM_MIN_TIER]
            out.append(_recap("big_rooms", w.key, f"The biggest shows in Philly {w.when}", "the biggest shows", w, big))
            small = [e for e in music if (e.get("fans") or 0) >= SMALL_ROOM_FANS and (e.get("venue_tier") or 100) <= SMALL_ROOM_MAX_TIER]
            out.append(_recap("small_rooms", w.key, f"Big names, small rooms: {month}", "big names in small rooms", w, small))
            lineups = [e for e in music if (e.get("support") or "").count(", ") >= 1]
            out.append(_recap("lineups", w.key, f"Stacked lineups in Philly {w.when}", "stacked lineups", w, lineups))
            back = [e for e in music if (history_facts(e, today.isoformat()) or {}).get("is_comeback")]
            rec = _recap("comeback", w.key, f"Back in Philly after years: {month}", "artists back in Philly after years",
                         w, back)
            if rec:  # each show says when they last played here (setlist.fm)
                hist = {e["id"]: history_facts(e, today.isoformat()) for e in back}
                for s, eid in zip(rec["facts"]["shows"], rec["event_ids"]):
                    h = hist.get(eid)
                    if h:
                        s.update(last_philly_show_year=h["last_philly_show_year"],
                                 years_since_last_philly_show=h["years_since_last_philly_show"])
                out.append(rec)
            local = [e for e in events if is_experience(e) and _usable(e)
                     and e["local_date"] <= w.end.isoformat() and (e.get("end_date") or e["local_date"]) >= w.start.isoformat()]
            out.append(_recap("local", w.key, f"Markets & festivals in Philly {w.when}", "markets and festivals", w,
                              local, min_shows=2, picks=False))
    return [r for r in out if r]


def _runs(nights: list[dict]) -> list[list[dict]]:
    """Dates split where the gap is over RUN_GAP_DAYS: a run of nights, not a monthly series."""
    runs = [[nights[0]]]
    for e in nights[1:]:
        gap = (date.fromisoformat(e["local_date"]) - date.fromisoformat(runs[-1][-1]["local_date"])).days
        if gap > RUN_GAP_DAYS:
            runs.append([])
        runs[-1].append(e)
    return runs


def _multi_night(events: list[dict], today: date) -> list[dict]:
    """An artist with a run of two or more Philly dates coming up (no more than RUN_GAP_DAYS apart): every date in
    one video. A monthly club night or a series spread over the season isn't a run."""
    horizon = (today + timedelta(days=MULTI_NIGHT_DAYS)).isoformat()
    by_act = defaultdict(list)
    for e in events:
        if (is_music(e) or is_comedy(e)) and _usable(e) and e["local_date"] <= horizon and e.get("headliner_id"):
            by_act[e["headliner_id"]].append(e)
    out = []
    for act, evs in by_act.items():
        every = sorted({(e["local_date"], e["venue_name"]): e for e in evs}.values(), key=lambda e: e["local_date"])
        nights = next((r for r in _runs(every) if len({e["local_date"] for e in r}) >= 2), None)
        if not nights:
            continue
        name, n = nights[0]["headliner"], len({e["local_date"] for e in nights})
        first, last = date.fromisoformat(nights[0]["local_date"]), date.fromisoformat(nights[-1]["local_date"])
        venues = {e["venue_name"] for e in nights}
        where = f" at {nights[0]['venue_name']}" if len(venues) == 1 else ""
        rec = _recap("multi_night", f"{act}:{first}", f"{name}: {n} nights in Philly{where} ({_span(first, last)})",
                     f"{name}'s Philly dates", None, nights, when=_span(first, last), min_shows=2, picks=False,
                     extra={"artist": name, "night_count": n},
                     post_by=et_iso(first - timedelta(days=3), 18),
                     expires=et_iso(nights[-2]["local_date"], 23, 59))  # useful while another night is still ahead
        if rec:
            rec["score"] = max(e["score"] for e in nights) + 5
            out.append(rec)
    return out


def _afterparties(events: list[dict], today: date) -> list[dict]:
    """After a big concert: shows that start late the same night at other venues (or are named an afterparty)."""
    horizon = (today + timedelta(days=AFTER_DAYS)).isoformat()
    by_night = defaultdict(list)
    for e in events:
        if is_music(e) and _usable(e):
            by_night[e["local_date"]].append(e)
    out = []
    for night, evs in by_night.items():
        if night > horizon:
            continue
        late = [e for e in evs if (e.get("local_time") or "") >= LATE_START or AFTERPARTY.search(e["name"] or "")]
        for big in _one_per_act([e for e in evs if e["score"] >= AFTER_MIN_SCORE and e not in late]):
            after = [e for e in late if e["venue_name"] != big["venue_name"] and e["headliner"] != big["headliner"]]
            if not after:
                continue
            named = [e for e in after if AFTERPARTY.search(e["name"] or "")]
            if len(after) < 2 and not named:
                continue
            d = date.fromisoformat(night)
            rec = _recap("afterparty", f"{big['id']}", f"After {big['headliner']} ({fmt_date(night)}): late shows that night",
                         f"late shows after {big['headliner']}", None, after, when=fmt_date(night), min_shows=1,
                         picks=False, extra={"headliner": big["headliner"], "night": fmt_date(night),
                                             "concert": event_facts(big),
                                             "named_afterparties": [e["name"] for e in named]},
                         post_by=et_iso(d - timedelta(days=1), 18), expires=et_iso(d, 21))
            if rec:
                rec["score"] = big["score"]
                rec["event_ids"] = [big["id"], *rec["event_ids"]]
                out.append(rec)
    return out


def _holiday_shows(events: list[dict], today: date) -> list[dict]:
    """Holiday-season shows (by name or listed genre), from mid-November to New Year's Day."""
    start = date(today.year, 11, 15)
    if today < start - timedelta(days=30) and today.month != 1:
        return []
    season_end = date(today.year + (today.month >= 11), 1, 1)
    w = Window(f"holiday_season:{season_end.year - 1}", "holiday", max(start, today), season_end, "this holiday season",
               et_iso(max(start, today), 12), et_iso(date(season_end.year - 1, 12, 20), 23, 59))
    shows = [e for e in events if _in(e, w) and _usable(e) and not is_game(e)
             and (HOLIDAY_WORDS.search(e["name"] or "") or e.get("genre") == "Holiday")]
    rec = _recap("holiday_shows", w.key, "Holiday shows in Philly this season", "holiday shows", w, shows)
    return [rec] if rec else []


def _just_announced(conn, events: list[dict], today: date) -> list[dict]:
    """Every notable Philly show announced in the last 7 days, in one video. One per week (Monday's date)."""
    since = datetime.combine(today - timedelta(days=7), datetime.min.time(), TZ).astimezone(timezone.utc).isoformat()
    new_ids = {r["event_id"] for r in conn.execute(
        "SELECT event_id FROM changes WHERE kind='new' AND at >= ?", (since,))}
    shows = [e for e in events if e["id"] in new_ids and _usable(e) and (is_music(e) or is_comedy(e))
             and e["score"] >= config.NEW_TOUR_MIN_SCORE]
    monday = today - timedelta(days=today.weekday())
    rec = _recap("just_announced", f"{monday}", f"Just announced in Philly this week ({_plural(len(_one_per_act(shows)[:LIST_MAX]), 'show')})",
                 "shows just announced for Philly", None, shows, when=f"the week of {monday:%b} {monday.day}",
                 post_by=et_iso(today + timedelta(days=1), 12), expires=et_iso(monday + timedelta(days=9), 23, 59),
                 min_shows=4)
    return [rec] if rec else []


def _on_sale(conn, events: list[dict], now: datetime) -> list[dict]:
    """Ticket sales (presales and general on-sales) opening in the next 7 days."""
    horizon = now + timedelta(days=7)
    by_id = {e["id"]: e for e in events}
    opens: dict[str, tuple[str, str]] = {}
    rows = [(r["event_id"], r["name"], r["start_utc"]) for r in conn.execute("SELECT event_id, name, start_utc FROM presales")]
    rows += [(e["id"], "General public on-sale", e["onsale_start"]) for e in events if e.get("onsale_start")]
    for eid, name, start in rows:
        if eid not in by_id or not start:
            continue
        t = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if now < t <= horizon and (eid not in opens or start < opens[eid][1]):
            opens[eid] = (name, start)
    shows = [by_id[i] for i in opens if _usable(by_id[i]) and by_id[i]["score"] >= config.NEW_TOUR_MIN_SCORE]
    today = now.astimezone(TZ).date()
    monday = today - timedelta(days=today.weekday())
    rec = _recap("on_sale", f"{monday}", "Tickets going on sale this week", "tickets going on sale", None, shows,
                 when=f"the week of {monday:%b} {monday.day}", post_by=et_iso(today + timedelta(days=1), 10),
                 expires=et_iso(monday + timedelta(days=6), 23, 59), picks=False)
    if rec:
        for s, eid in zip(rec["facts"]["shows"], rec["event_ids"]):
            s["sale"] = opens[eid][0]
            s["sale_opens"] = fmt_utc_local(opens[eid][1])
    return [rec] if rec else []


def detect_all(conn, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(TZ).date()
    events = upcoming_events(conn, today.isoformat())
    out = (_by_window(events, windows(today), today) + _multi_night(events, today) + _afterparties(events, today)
           + _holiday_shows(events, today) + _just_announced(conn, events, today) + _on_sale(conn, events, now))
    seen, unique = set(), []
    for r in out:  # "Rock in December" listing the same shows as "Rock, December 1-15": one video, keep the first
        f = r["facts"]
        same = (f["recap_type"], f.get("genre") or f.get("venue") or f.get("city") or f.get("artist"),
                frozenset(r["event_ids"]))
        if same not in seen:
            seen.add(same)
            unique.append(r)
    return unique


# ---------- drafting and the queue ----------

def guide(facts: dict) -> str:
    """The format guide for a recap (llm.build_prompt)."""
    rtype = facts.get("recap_type")
    vals = {k: v for k, v in facts.items() if isinstance(v, (str, int))}
    try:
        body = GUIDES.get(rtype, "").format(**vals)
    except (KeyError, IndexError):
        body = GUIDES.get(rtype, "")
    return (f"Video: a Philly recap: {facts.get('theme')} ({facts.get('when')}). {body}\n"
            "Say the stretch of dates the way `when` does (it's anchored to dates, so it stays right whenever it's "
            "posted). Give each show its date as written (\"Fri, Dec 4\"), not just a weekday: a list can cover "
            "several weeks. Keep each show to one short line, in the order given. Don't add shows, ranks, sales, "
            "crowds, genres or opinions about the artists beyond the theme.\n"
            "Example hook: \"[Theme] in Philly [when]\"\n"
            "Example script: \"Here's [theme] in Philly [when]. [Day], [Artist] at [Venue]. [Day], [Artist] at "
            "[Venue]. Which one are you going to?\"")


def story(facts: dict) -> tuple[float, str]:
    return STORY.get(facts.get("recap_type"), (45, "Recap"))


def angles_for(facts: dict) -> list[dict]:
    """Ways to frame a recap, from its facts only (angles.py)."""
    from .angles import _angle
    from .templates import MAX_HOOK_WORDS
    theme, when, n = facts.get("theme", ""), facts.get("when", ""), facts.get("show_count") or len(facts.get("shows", []))
    rtype = facts.get("recap_type")

    def fit(*hooks):  # the first one short enough for a hook
        return next((h for h in hooks if len(h.split()) <= MAX_HOOK_WORDS), hooks[-1])
    if rtype == "multi_night":
        a = facts["artist"]
        return [_angle("news", "Every date", fit(f"{a} has {facts['night_count']} Philly dates", f"{a}: every Philly date"),
                       "Every date and venue on screen. Made to be saved.", "slideshow", "Save this and tag who's coming."),
                _angle("fan", "Pick a night", fit(f"{a} fans: which Philly night are you going to?"),
                       "Ask which night, list them, let the comments argue.", "face-to-camera", "Comment your night.")]
    if rtype == "afterparty":
        h = facts["headliner"]
        return [_angle("utility", "After the show", fit(f"Going to {h}? Here's where the night keeps going",
                                                        f"After {h}: where to go next"),
                       "Late shows the same night, start times on screen.", "slideshow", "Send this to your group chat."),
                _angle("fan", "Not ready to go home", fit(f"Not ready to go home after {h}?"),
                       "Talk to people with tickets: the late options that night.", "face-to-camera",
                       "Tag who you're going with.")]
    head = theme[:1].upper() + theme[1:]
    out = [
        _angle("news", "The list", fit(f"{n} {theme} in Philly {when}", f"{head} in Philly {when}", f"{head}: {when}"),
               "One line per show, day and venue on screen.", "slideshow", "Save this for later."),
        _angle("fan", "Ask the comments", fit(f"Which of these are you going to? {head}, {when}", f"Which one are you going to?"),
               "Ask, list the options, let the comments argue.", "face-to-camera", "Comment your pick."),
    ]
    if facts.get("picks"):
        k = facts["pick_count"]
        out.append(_angle("picks", f"Top {k}", fit(f"My {k} picks: {theme}, {when}", f"My {k} picks for Philly {when}"),
                          f"Only the {k} biggest, best first.", "slideshow", "Save this and tell me what I missed."))
    return out


def in_your_lane(events: list[dict]) -> bool:
    """Half or more of its shows in your lane (scoring's `niche`): what puts a recap on "What to post" too."""
    music = [e for e in events if is_music(e)]
    return bool(music) and sum(bool(e.get("niche")) for e in music) * 2 >= len(music)


def prune(conn, now: str | None = None, days: int = 7) -> int:
    """Delete recaps that expired over `days` ago and that you never touched (no draft, post, star or note):
    there are many, and they're rebuilt from the data anyway."""
    cutoff = (datetime.fromisoformat(now or now_iso()) - timedelta(days=days)).isoformat()
    ids = [r["id"] for r in conn.execute(
        """SELECT a.id FROM alerts a WHERE a.kind = ? AND a.expires_at < ? AND a.shortlisted = 0 AND a.note IS NULL
             AND a.planned_for IS NULL
             AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.alert_id = a.id)
             AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.alert_id = a.id)""", (KIND, cutoff))]
    for i in ids:
        conn.execute("DELETE FROM alert_history WHERE alert_id=?", (i,))
        conn.execute("DELETE FROM alerts WHERE id=?", (i,))
    conn.commit()
    return len(ids)
