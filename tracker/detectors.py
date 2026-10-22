"""Detectors: plain rules over the database that decide what's worth a video.

Each detector returns alert dicts:
    {"key": dedupe key, "kind": ..., "title": ..., "score": ..., "facts": {...},
     "event_ids": [...], "post_by": UTC ISO or None, "expires_at": UTC ISO or None}
`facts` is everything the LLM is allowed to say, already formatted for humans
(e.g. "Sat, Nov 8 · 8:00 PM"), so the model never has to do date math.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .scoring import is_game, upcoming_events
from .store import now_iso

TZ = ZoneInfo(config.TIMEZONE)
log = logging.getLogger(__name__)


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


def utc_iso(iso: str) -> str:
    """'2026-10-22T14:00:00Z' -> '2026-10-22T14:00:00+00:00' (the format stored everywhere else)."""
    return parse_utc(iso).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def et_iso(d: date | str, hour: int, minute: int = 0) -> str:
    """A Philly wall-clock time on day `d`, as UTC ISO. Used for when a video should be up by."""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ).astimezone(timezone.utc).isoformat()


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
        # A Jingle Ball slot isn't "their own show": scripts say "last time was a 9-act lineup".
        "last_philly_show_lineup_size": e.get("last_area_show_acts"),
        "last_philly_show_was_multi_act": (e.get("last_area_show_acts") or 0) >= config.MULTI_ACT_MIN,
        "years_since_last_philly_show": years,
        "is_comeback": years >= config.COMEBACK_MIN_YEARS,
        "source": "setlist.fm",
    }


def rotation_prefix(my_plays: int) -> str:
    return "🎧 " if my_plays >= config.ROTATION_MIN_PLAYS else ""


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
        my_plays = max(e.get("my_plays") or 0 for e in events)
        if best < config.NEW_TOUR_MIN_SCORE and my_plays < config.ROTATION_MIN_PLAYS:
            continue  # artists in your rotation alert regardless of how big they are
        events.sort(key=lambda e: e["local_date"])
        head = events[0]
        presales = _presales_for(conn, [e["id"] for e in events])
        history = history_facts(head, today)
        alerts.append({
            "key": f"new:{artist_id}:{head['local_date']}",
            "kind": "new_tour",
            "title": f"{rotation_prefix(my_plays)}{comeback_prefix(history)}{'Added date' if prior else 'New Philly show'}: {head['headliner']}"
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
                "your_plays": my_plays,
            },
            "event_ids": [e["id"] for e in events],
            "expires_at": et_iso(events[-1]["local_date"], 23, 59),
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
        my_plays = max(e.get("my_plays") or 0 for e in evs)
        if not group["triggered"] or (best < config.PRESALE_MIN_SCORE and my_plays < config.ROTATION_MIN_PLAYS):
            continue
        # Earliest first; at the same time, presales before the general on-sale.
        windows = sorted(group["windows"], key=lambda w: (w[1], w[0] == "General public on-sale", w[0]))
        head = evs[0]
        first_name, first_start = windows[0]
        history = history_facts(head, today)
        alerts.append({
            "key": f"sale:{artist_key}:{head['local_date']}",
            "kind": "presale_soon",
            "title": f"{rotation_prefix(my_plays)}{comeback_prefix(history)}Tickets for {head['headliner']} open {fmt_utc_local(first_start)}"
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
                "your_plays": my_plays,
            },
            "event_ids": [e["id"] for e in evs],
            "post_by": utc_iso(first_start),               # the reminder is only useful before it opens
            "expires_at": utc_iso(max(st for _, st in windows)),
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
        "event_ids": [e["id"] for e in picks],
        "post_by": et_iso(fri, 17),                        # people plan their weekend before Friday evening
        "expires_at": et_iso(fri + timedelta(days=1), 18),
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
            "event_ids": [e["id"] for e in top],
            "post_by": et_iso(f"{ym}-01", 0),              # before the month starts
            "expires_at": et_iso(f"{ym}-15", 23, 59),      # matches "past mid-month is too late" above
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
            "event_ids": [e["id"]],
            "expires_at": et_iso(e["local_date"], 23, 59),
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
            "event_ids": [e["id"] for e in games],
            "post_by": et_iso(games[0]["local_date"], 12),
            "expires_at": et_iso(games[-1]["local_date"], 23, 59),
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
            "event_ids": [e["id"]],
            "post_by": et_iso(e["local_date"], 12),
            "expires_at": et_iso(e["local_date"], 23, 59),
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


# Facts that change on every run without anything real happening; ignored when deciding whether an alert changed.
VOLATILE_FACTS = {"hours_until_open", "your_plays"}


def _stable(facts: dict) -> dict:
    # None and missing mean the same (alerts raised before a fact existed don't have the key at all)
    return {k: v for k, v in facts.items() if k not in VOLATILE_FACTS and v is not None}


def save_alerts(conn: sqlite3.Connection, alerts: list[dict]) -> list[int]:
    """Insert new alerts; refresh the facts of ones already raised. Returns ids of newly created alerts.

    A weekend roundup raised on Wednesday is re-detected on Thursday and Friday; if a show was added or
    cancelled meanwhile, its facts, title and score are updated (the old version goes to alert_history).
    Drafts are never touched: each keeps the facts it was written from, and the dashboard shows what
    changed since (facts_diff). Dismissed alerts and alerts you've logged a post for are left as they were.
    """
    created, refreshed = [], 0
    for a in alerts:
        meta = (json.dumps(a["event_ids"]) if a.get("event_ids") else None, a.get("post_by"), a.get("expires_at"))
        old = conn.execute("SELECT * FROM alerts WHERE key=?", (a["key"],)).fetchone()
        if old is None:
            cur = conn.execute(
                """INSERT INTO alerts (key, kind, title, score, facts, created_at, event_ids, post_by, expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (a["key"], a["kind"], a["title"], a["score"], json.dumps(a["facts"]), now_iso(), *meta))
            created.append(cur.lastrowid)
            continue
        # Links and timing aren't facts the video states: keep them current (alerts from before they existed get them here)
        if meta != (old["event_ids"], old["post_by"], old["expires_at"]):
            conn.execute("UPDATE alerts SET event_ids=?, post_by=?, expires_at=? WHERE id=?", (*meta, old["id"]))
        posted = conn.execute("SELECT 1 FROM posts WHERE alert_id=? LIMIT 1", (old["id"],)).fetchone()
        if old["status"] == "dismissed" or posted or _stable(json.loads(old["facts"])) == _stable(a["facts"]):
            continue
        conn.execute("INSERT INTO alert_history (alert_id, title, score, facts, replaced_at) VALUES (?,?,?,?,?)",
                     (old["id"], old["title"], old["score"], old["facts"], now_iso()))
        conn.execute("UPDATE alerts SET title=?, score=?, facts=?, updated_at=? WHERE id=?",
                     (a["title"], a["score"], json.dumps(a["facts"]), now_iso(), old["id"]))
        refreshed += 1
    conn.commit()
    if refreshed:
        log.info("Refreshed facts on %d existing alerts", refreshed)
    return created


def _show_label(s: dict) -> str:
    return f"{s.get('artist') or s.get('event_name')} ({s.get('date')})"


def _show_key(s: dict) -> tuple:
    return (s.get("artist") or s.get("event_name"), s.get("date"), s.get("venue"))


SHOW_FIELDS = ("time", "status", "price", "venue", "support")  # per-show facts worth reporting when they change


def facts_diff(old: dict, new: dict) -> list[str]:
    """What changed between two versions of an alert's facts, as short lines for the dashboard
    ("Added: Nova Vale (Sat, Oct 24)", "Removed: ...", "sale_opens: A → B"). Empty if nothing material."""
    out = []
    old_shows = old.get("shows") or ([old["show"]] if old.get("show") else [])
    new_shows = new.get("shows") or ([new["show"]] if new.get("show") else [])
    before = {_show_key(s): s for s in old_shows}
    after = {_show_key(s): s for s in new_shows}
    out += [f"Added: {_show_label(s)}" for k, s in after.items() if k not in before]
    out += [f"Removed: {_show_label(s)}" for k, s in before.items() if k not in after]
    for k in before.keys() & after.keys():
        for f in SHOW_FIELDS:
            if before[k].get(f) != after[k].get(f):
                out.append(f"{_show_label(after[k])}: {f} {before[k].get(f) or '—'} → {after[k].get(f) or '—'}")
    for key in ("all_sales", "presales"):
        was = {(w["name"], w.get("opens") or w.get("starts")) for w in old.get(key) or []}
        now = {(w["name"], w.get("opens") or w.get("starts")) for w in new.get(key) or []}
        out += [f"New sale: {n}, {t}" for n, t in sorted(now - was)]
        out += [f"Sale no longer listed: {n}, {t}" for n, t in sorted(was - now)]
    was_last, now_last = ((f.get("history") or {}).get("last_philly_show") for f in (old, new))
    if was_last != now_last:
        out.append(f"Last Philly show (setlist.fm): {was_last or '—'} → {now_last or '—'}")
    skip = VOLATILE_FACTS | {"shows", "show", "all_sales", "presales", "history"}
    for k in sorted((old.keys() | new.keys()) - skip):
        if old.get(k) != new.get(k):
            if isinstance(old.get(k), (dict, list)) or isinstance(new.get(k), (dict, list)):
                out.append(f"{k} changed")
            else:
                out.append(f"{k}: {old.get(k) if old.get(k) is not None else '—'} → {new.get(k) if new.get(k) is not None else '—'}")
    return out
