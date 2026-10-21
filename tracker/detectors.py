"""Detectors: plain rules over the database that decide what's worth a video.

Each detector returns alert dicts:
    {"key": dedupe key, "kind": ..., "title": ..., "score": ..., "facts": {...}}
`facts` is everything the LLM is allowed to say, already formatted for humans
(e.g. "Sat, Nov 8 · 8:00 PM"), so the model never has to do date math.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .scoring import upcoming_events
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
    return {k: v for k, v in f.items() if v}


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
        if e["id"] in new_ids and e["headliner_id"]:
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
        alerts.append({
            "key": f"new:{artist_id}:{head['local_date']}",
            "kind": "new_tour",
            "title": f"{'Added date' if prior else 'New Philly show'}: {head['headliner']}"
                     f" — {fmt_date(head['local_date'])} at {head['venue_name']}",
            "score": best + (0 if prior else 10),
            "facts": {
                "format": "new_tour",
                "artist": head["headliner"],
                "already_had_philly_dates": bool(prior),
                "tour_dates_listed_worldwide": head["tm_upcoming_total"],
                "shows": [event_facts(e) for e in events],
                "public_onsale": fmt_utc_local(head["onsale_start"]) if head["onsale_start"] else None,
                "presales": presales,
            },
        })
    return alerts


def _presales_for(conn, event_ids: list[str]) -> list[dict]:
    rows = conn.execute(
        "SELECT DISTINCT name, start_utc, end_utc FROM presales WHERE event_id IN (%s) ORDER BY start_utc"
        % ",".join("?" * len(event_ids)), event_ids).fetchall()
    return [{"name": r["name"], "starts": fmt_utc_local(r["start_utc"])} for r in rows if r["start_utc"]]


def detect_sales_soon(conn: sqlite3.Connection, now: datetime, today: str) -> list[dict]:
    """Presales or public on-sales opening within the next PRESALE_WINDOW_HOURS."""
    horizon = now + timedelta(hours=config.PRESALE_WINDOW_HOURS)
    events = {e["id"]: e for e in upcoming_events(conn, today)}

    windows = []  # (event, sale_name, start_iso)
    for r in conn.execute("SELECT event_id, name, start_utc FROM presales WHERE start_utc IS NOT NULL"):
        windows.append((r["event_id"], r["name"], r["start_utc"]))
    for e in events.values():
        if e["onsale_start"]:
            windows.append((e["id"], "General public on-sale", e["onsale_start"]))

    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for event_id, name, start in windows:
        e = events.get(event_id)
        if not e or e["status"] in ("cancelled", "postponed"):
            continue
        if now < parse_utc(start) <= horizon:
            grouped[(e["headliner_id"] or e["id"], name, start)].append(e)

    alerts = []
    for (artist_key, name, start), evs in grouped.items():
        evs.sort(key=lambda e: e["local_date"])
        head = evs[0]
        alerts.append({
            "key": f"sale:{artist_key}:{name}:{start}",
            "kind": "presale_soon",
            "title": f"{name} for {head['headliner']} opens {fmt_utc_local(start)}",
            "score": max(e["score"] for e in evs),
            "facts": {
                "format": "presale",
                "artist": head["headliner"],
                "sale_name": name,
                "sale_opens": fmt_utc_local(start),
                "hours_until_open": round((parse_utc(start) - now).total_seconds() / 3600),
                "note": "Presale codes are not in our data. Do not invent one; tell viewers to check the artist/TM page.",
                "shows": [event_facts(e) for e in evs],
            },
        })
    return alerts


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
    this_month = today.strftime("%Y-%m")
    next_month = (today.replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in upcoming_events(conn, today.isoformat()):
        ym = e["local_date"][:7]
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
