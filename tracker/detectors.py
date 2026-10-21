"""Detectors: plain rules over the database that decide what's worth a video.

Each detector returns alert dicts:
    {"key": dedupe key, "kind": ..., "title": ..., "score": ..., "facts": {...}}
`facts` is everything the LLM is allowed to say, already formatted for humans
(e.g. "Sat, Nov 8 · 8:00 PM"), so the model never has to do date math.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .scoring import is_game, upcoming_events
from .store import now_iso

TZ = ZoneInfo(config.TIMEZONE)


# ---------- formatting helpers (facts are pre-formatted so the LLM can copy them) ----------

def fmt_date(d: str) -> str:
    dt = date.fromisoformat(d)
    return f"{dt:%a}, {dt:%b} {dt.day}"


def fmt_time(t: str | None) -> str | None:
    if not t:
        return None
    dt = datetime.strptime(t[:5], "%H:%M")
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_utc_local(iso: str) -> str:
    """'2026-10-23T14:00:00Z' -> 'Thu, Oct 23 at 10:00 AM ET'."""
    dt = parse_utc(iso).astimezone(TZ)
    return f"{dt:%a}, {dt:%b} {dt.day} at {dt.strftime('%I:%M %p').lstrip('0')} ET"


def parse_utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def fmt_price(lo, hi) -> str | None:
    def money(x):  # $45, but $59.50 stays exact
        return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"
    if lo is None:
        return None
    if hi and hi != lo:
        return f"{money(lo)}–{money(hi)}"
    return money(lo)


def event_facts(e: dict) -> dict:
    """The per-show facts we hand to the LLM / dashboard."""
    f = {
        "artist": e["headliner"],
        "event_name": e["name"],
        "date": fmt_date(e["local_date"]),
        "time": fmt_time(e["local_time"]),
        "venue": e["venue_name"],
        "city": e["venue_city"],
        "genre": e["genre"],
        "price": fmt_price(e["price_min"], e["price_max"]),
        "status": e["status"],
        "url": e["url"],
        "support": e.get("support"),
    }
    if is_game(e):  # "Sixers vs. Cavaliers" reads better than "Philadelphia 76ers" in a roundup
        f.update(artist=matchup(e), opponent=e.get("opponent"), kind="game", support=None, genre=e["subgenre"])
    return {k: v for k, v in f.items() if v}


def team_short(name: str) -> str:
    return (config.SPORTS_TEAMS.get(name) or {}).get("short", name)


def opponent_short(name: str | None) -> str:
    """'Cleveland Cavaliers' -> 'Cavaliers' (NBA nicknames are the last word, incl. 'Trail Blazers' -> 'Blazers')."""
    return (name or "TBA").split()[-1]


def matchup(e: dict) -> str:
    return f"{team_short(e['headliner'])} vs. {opponent_short(e.get('opponent'))}"


def history_facts(e: dict, today: str) -> dict | None:
    """'Last Philly show' from setlist.fm, or None. Only a *found* show is reported: setlist.fm is
    crowd-sourced, so "nothing found" never becomes "first time ever"."""
    last = e.get("last_area_show_date")
    if not last:
        return None
    d, t = date.fromisoformat(last), date.fromisoformat(today)
    years = (t - d).days // 365
    return {
        "last_philly_show": f"{d:%b} {d.day}, {d.year}",
        "last_philly_show_year": d.year,
        "last_philly_venue": e.get("last_area_show_venue"),
        "years_since_last_philly_show": years,
        "is_comeback": years >= config.COMEBACK_MIN_YEARS,
        "source": "setlist.fm",
    }


def comeback_prefix(history: dict | None) -> str:
    if history and history["is_comeback"]:
        return f"Back after {history['years_since_last_philly_show']} years: "
    return ""


def comeback_bonus(history: dict | None) -> float:
    return config.COMEBACK_BONUS if history and history["is_comeback"] else 0


def month_label(ym: str) -> str:
    return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")


# ---------- detectors ----------

def detect_new_tours(conn: sqlite3.Connection, run_id: int, today: str) -> list[dict]:
    """New Philly-area dates this run for headliners who had nothing announced here before."""
    new_ids = {r["event_id"] for r in conn.execute(
        "SELECT event_id FROM changes WHERE run_id=? AND kind='new'", (run_id,))}
    if not new_ids:
        return []
    by_artist: dict[str, list[dict]] = defaultdict(list)
    for e in upcoming_events(conn, today):
        if e["id"] in new_ids and e["headliner_id"] and not is_game(e):  # schedule releases aren't tours
            by_artist[e["headliner_id"]].append(e)

    alerts = []
    for artist_id, events in by_artist.items():
        # Did this artist already have a Philly event before this run? Then it's just an added date.
        prior = conn.execute(
            """SELECT 1 FROM event_attractions ea JOIN events e ON e.id = ea.event_id
               WHERE ea.attraction_id=? AND ea.position=0 AND e.id NOT IN (%s) LIMIT 1"""
            % ",".join("?" * len(events)), [artist_id, *[e["id"] for e in events]]).fetchone()
        best = max(e["score"] for e in events)
        if best < config.NEW_TOUR_MIN_SCORE:
            continue
        events.sort(key=lambda e: e["local_date"])
        head = events[0]
        presales = _presales_for(conn, [e["id"] for e in events])
        history = history_facts(head, today)
        alerts.append({
            "key": f"new:{artist_id}:{head['local_date']}",
            "kind": "new_tour",
            "title": f"{comeback_prefix(history)}{'Added date' if prior else 'New Philly show'}: {head['headliner']}"
                     f" — {fmt_date(head['local_date'])} at {head['venue_name']}",
            "score": best + (0 if prior else 10) + comeback_bonus(history),
            "facts": {
                "format": "new_tour",
                "artist": head["headliner"],
                "already_had_philly_dates": bool(prior),
                "tour_dates_listed_worldwide": head["tm_upcoming_total"],
                "shows": [event_facts(e) for e in events],
                "public_onsale": fmt_utc_local(head["onsale_start"]) if head["onsale_start"] else None,
                "presales": presales,
                "history": history,
            },
        })
    return alerts


def _presales_for(conn, event_ids: list[str]) -> list[dict]:
    rows = conn.execute(
        "SELECT DISTINCT name, start_utc, end_utc FROM presales WHERE event_id IN (%s) ORDER BY start_utc"
        % ",".join("?" * len(event_ids)), event_ids).fetchall()
    return [{"name": r["name"], "starts": fmt_utc_local(r["start_utc"])} for r in rows if r["start_utc"]]


def detect_sales_soon(conn: sqlite3.Connection, now: datetime, today: str) -> list[dict]:
    """One alert per artist when any presale/on-sale for their Philly show(s) opens soon.

    Triggered by a window opening within PRESALE_WINDOW_HOURS; the facts then list every
    upcoming window for those shows (presales + general on-sale) so the video can cover both.
    VIP/package on-sales are ignored, and so are small acts (PRESALE_MIN_SCORE).
    """
    horizon = now + timedelta(hours=config.PRESALE_WINDOW_HOURS)
    events = {e["id"]: e for e in upcoming_events(conn, today)}

    # artist -> {"events": {...}, "windows": {(name, start)}}
    by_artist: dict[str, dict] = defaultdict(lambda: {"events": {}, "windows": set(), "triggered": False})

    def add(event_id, name, start):
        e = events.get(event_id)
        if not e or not start or e["status"] in ("cancelled", "postponed") or _ignored_sale(name):
            return
        opens = parse_utc(start)
        if opens <= now:
            return
        group = by_artist[e["headliner_id"] or e["id"]]
        group["events"][e["id"]] = e
        group["windows"].add((name, start))
        if opens <= horizon:
            group["triggered"] = True

    for r in conn.execute("SELECT event_id, name, start_utc FROM presales"):
        add(r["event_id"], r["name"], r["start_utc"])
    for e in events.values():
        add(e["id"], "General public on-sale", e["onsale_start"])

    alerts = []
    for artist_key, group in by_artist.items():
        evs = sorted(group["events"].values(), key=lambda e: e["local_date"])
        best = max(e["score"] for e in evs)
        if not group["triggered"] or best < config.PRESALE_MIN_SCORE:
            continue
        # Earliest first; at the same time, presales before the general on-sale.
        windows = sorted(group["windows"], key=lambda w: (w[1], w[0] == "General public on-sale", w[0]))
        head = evs[0]
        first_name, first_start = windows[0]
        history = history_facts(head, today)
        alerts.append({
            "key": f"sale:{artist_key}:{head['local_date']}",
            "kind": "presale_soon",
            "title": f"{comeback_prefix(history)}Tickets for {head['headliner']} open {fmt_utc_local(first_start)}"
                     f" ({first_name})",
            "score": best + comeback_bonus(history),
            "facts": {
                "format": "presale",
                "artist": head["headliner"],
                "sale_name": first_name,
                "sale_opens": fmt_utc_local(first_start),
                "hours_until_open": round((parse_utc(first_start) - now).total_seconds() / 3600),
                "all_sales": [{"name": n, "opens": fmt_utc_local(st)} for n, st in windows],
                "shows": [event_facts(e) for e in evs],
                "history": history,
            },
        })
    return alerts


_IGNORED_SALES = [re.compile(p, re.I) for p in config.IGNORED_SALE_PATTERNS]


def _ignored_sale(name: str) -> bool:
    return any(p.search(name or "") for p in _IGNORED_SALES)


def upcoming_weekend(today: date) -> tuple[date, date]:
    """Fri..Sun of this week (if today is Fri-Sun) or the coming one."""
    if today.weekday() >= 4:
        fri = today - timedelta(days=today.weekday() - 4)
    else:
        fri = today + timedelta(days=4 - today.weekday())
    return fri, fri + timedelta(days=2)


def detect_weekend(conn: sqlite3.Connection, today: date) -> list[dict]:
    """Weekend roundup. Created from Wednesday on so the lineup is mostly settled."""
    if today.weekday() < 2:
        return []
    fri, sun = upcoming_weekend(today)
    picks, per_genre = [], defaultdict(int)
    for e in upcoming_events(conn, today.isoformat()):  # already sorted by score
        if not (fri.isoformat() <= e["local_date"] <= sun.isoformat()) or e["status"] == "cancelled":
            continue
        g = e["genre"] or "Other"
        if per_genre[g] >= 2:  # keep the mix varied
            continue
        per_genre[g] += 1
        picks.append(e)
        if len(picks) >= config.WEEKEND_TOP_N:
            break
    if len(picks) < 3:
        return []
    picks.sort(key=lambda e: (e["local_date"], e["local_time"] or ""))
    return [{
        "key": f"weekend:{fri.isoformat()}",
        "kind": "weekend",
        "title": f"Weekend roundup: {fmt_date(fri.isoformat())} – {fmt_date(sun.isoformat())}",
        "score": round(sum(e["score"] for e in picks) / len(picks), 1),
        "facts": {
            "format": "weekend",
            "weekend": f"{fmt_date(fri.isoformat())} – {fmt_date(sun.isoformat())}",
            "shows": [event_facts(e) for e in picks],
        },
    }]


def detect_stacked_months(conn: sqlite3.Connection, today: date) -> list[dict]:
    """A genre with lots of notable shows in this month or next."""
    next_month = (today.replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
    # Past mid-month, "this month is stacked" comes too late to be useful; look at next month only.
    this_month = today.strftime("%Y-%m") if today.day <= 15 else next_month
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in upcoming_events(conn, today.isoformat()):
        ym = e["local_date"][:7]
        if is_game(e):
            continue  # "stacked month" is a music format
        if ym in (this_month, next_month) and e["genre"] and e["score"] >= config.STACKED_MONTH_MIN_SCORE:
            if e["status"] != "cancelled":
                buckets[(ym, e["genre"])].append(e)

    alerts = []
    for (ym, genre), evs in buckets.items():
        # one entry per artist (multi-night runs count once)
        seen, unique = set(), []
        for e in evs:
            if e["headliner"] not in seen:
                seen.add(e["headliner"])
                unique.append(e)
        if len(unique) < config.STACKED_MONTH_MIN_EVENTS:
            continue
        top = sorted(unique, key=lambda e: e["score"], reverse=True)[:10]
        top.sort(key=lambda e: e["local_date"])
        alerts.append({
            "key": f"stacked:{ym}:{genre}",
            "kind": "stacked_month",
            "title": f"{month_label(ym)} is stacked for {genre}: {len(unique)} notable shows",
            "score": round(sum(e["score"] for e in top) / len(top), 1) + len(unique),
            "facts": {
                "format": "stacked_month",
                "month": month_label(ym),
                "genre": genre,
                "notable_show_count": len(unique),
                "shows": [event_facts(e) for e in top],
            },
        })
    return alerts


def detect_status_changes(conn: sqlite3.Connection, run_id: int, today: str) -> list[dict]:
    """Cancellations, postponements, reschedules and date moves for notable shows."""
    events = {e["id"]: e for e in upcoming_events(conn, today)}
    alerts = []
    for c in conn.execute(
            "SELECT * FROM changes WHERE run_id=? AND kind IN ('status','date')", (run_id,)):
        e = events.get(c["event_id"])
        if not e or e["score"] < config.NEW_TOUR_MIN_SCORE:
            continue
        if c["kind"] == "status" and c["new_value"] not in ("cancelled", "postponed", "rescheduled"):
            continue
        what = c["new_value"] if c["kind"] == "status" else "date changed"
        alerts.append({
            "key": f"status:{e['id']}:{c['kind']}:{c['new_value']}",
            "kind": "status_change",
            "title": f"{e['headliner']} at {e['venue_name']}: {what}",
            "score": e["score"],
            "facts": {
                "format": "status_change",
                "artist": e["headliner"],
                "change": what,
                "previous_value": c["old_value"],
                "show": event_facts(e),
            },
        })
    return alerts


def detect_team_week(conn: sqlite3.Connection, today: date) -> list[dict]:
    """'Sixers home games this week' (Mon-Sun), one alert per team per week, from the first run of the week."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    by_team: dict[str, list[dict]] = defaultdict(list)
    for e in upcoming_events(conn, today.isoformat()):
        if is_game(e) and e["local_date"] <= sunday.isoformat() and e["status"] != "cancelled":
            by_team[e["headliner"]].append(e)
    alerts = []
    for team, games in by_team.items():
        games.sort(key=lambda e: (e["local_date"], e["local_time"] or ""))
        alerts.append({
            "key": f"team_week:{team}:{monday.isoformat()}",
            "kind": "team_week",
            "title": f"{team_short(team)} home games this week: {len(games)}",
            "score": max(e["score"] for e in games),
            "facts": {
                "format": "team_week",
                "artist": team_short(team),
                "team": team,
                "week": f"{fmt_date(monday.isoformat())} – {fmt_date(sunday.isoformat())}",
                "shows": [event_facts(e) for e in games],
            },
        })
    return alerts


def detect_big_games(conn: sqlite3.Connection, today: date) -> list[dict]:
    """A marquee opponent visiting within BIG_GAME_WINDOW_DAYS. One alert per game."""
    horizon = (today + timedelta(days=config.BIG_GAME_WINDOW_DAYS)).isoformat()
    alerts = []
    for e in upcoming_events(conn, today.isoformat()):
        if not is_game(e) or e["local_date"] > horizon or e["status"] == "cancelled":
            continue
        if e.get("opponent") not in config.MARQUEE_OPPONENTS:
            continue
        alerts.append({
            "key": f"big_game:{e['id']}",
            "kind": "big_game",
            "title": f"Big game: {matchup(e)} — {fmt_date(e['local_date'])}",
            "score": e["score"],
            "facts": {
                "format": "big_game",
                "artist": team_short(e["headliner"]),
                "team": e["headliner"],
                "opponent": e["opponent"],
                "show": event_facts(e),
            },
        })
    return alerts


def run_all(conn: sqlite3.Connection, run_id: int, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    local_today = now.astimezone(TZ).date()
    today = local_today.isoformat()
    alerts = (
        detect_new_tours(conn, run_id, today)
        + detect_sales_soon(conn, now, today)
        + detect_weekend(conn, local_today)
        + detect_stacked_months(conn, local_today)
        + detect_status_changes(conn, run_id, today)
        + detect_team_week(conn, local_today)
        + detect_big_games(conn, local_today)
    )
    return alerts


def save_alerts(conn: sqlite3.Connection, alerts: list[dict]) -> list[int]:
    """Insert alerts, skipping ones we've already raised. Returns ids of newly created alerts."""
    created = []
    for a in alerts:
        cur = conn.execute(
            "INSERT OR IGNORE INTO alerts (key, kind, title, score, facts, created_at) VALUES (?,?,?,?,?,?)",
            (a["key"], a["kind"], a["title"], a["score"], json.dumps(a["facts"]), now_iso()),
        )
        if cur.rowcount:
            created.append(cur.lastrowid)
    conn.commit()
    return created
