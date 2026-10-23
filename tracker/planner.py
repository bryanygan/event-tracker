"""Editorial calendar (roadmap P1-13): what's planned, posted and due over the next two weeks.

A plan is two fields you set on an idea: `planned_for` (when you mean to post) and `series` (which recurring
series it belongs to; the format's default from config.SERIES_FOR_FORMAT when unset). Nothing posts at the
planned time and nothing here changes an idea, a draft or a logged post. The calendar is built on every request
from the queue (opportunities.queue) and the posts table, and points out:

- days with nothing planned or posted, and days with more than CALENDAR_MAX_PER_DAY planned
- a week heavy on one format, or light on your lane
- the same artist in two planned videos, or planned soon after you posted about them (single-artist videos)
- plans after an idea's post-by time or after it goes stale (also on the queue card, opportunities.evaluate)
- a series (weekly roundup...) whose last post is older than its cadence with nothing planned
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, opportunities
from .detectors import fmt_date

TZ = ZoneInfo(config.TIMEZONE)
DONE_STAGES = ("posted", "measured")


def _local_day(iso: str) -> date:
    return opportunities._dt(iso).astimezone(TZ).date()


def _focus(artists: list[str]) -> list[str]:
    """Artists a video is about: one or two (a tour, a presale). A roundup names many and repeats none on purpose."""
    return artists if len(artists) <= 2 else []


def _item(i: dict) -> dict:
    return {k: i[k] for k in ("alert_id", "kind", "format", "title", "stage", "band", "score", "post_by",
                              "expires_at", "planned_for", "series", "series_set", "lane", "caveats")}


def calendar(conn, now: datetime | None = None, start: date | None = None, days: int = config.CALENDAR_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(TZ).date()
    start = start or today - timedelta(days=today.weekday())  # Monday of this week
    end = start + timedelta(days=days)
    items = opportunities.queue(conn, now)
    by_id = {i["alert_id"]: i for i in items}

    posts = []
    for p in conn.execute("SELECT * FROM posts ORDER BY posted_at"):
        p = dict(p)
        facts = json.loads(p["facts_snapshot"]) if p["facts_snapshot"] else {}
        alert = by_id.get(p["alert_id"])
        posts.append({"id": p["id"], "alert_id": p["alert_id"], "title": p["title"], "posted_at": p["posted_at"],
                      "url": p["url"], "format": p["format"],
                      "series": p["series"] or config.SERIES_FOR_FORMAT.get(p["format"] or ""),
                      "lane": alert["lane"] if alert else None,
                      "artists": sorted(opportunities._artists(facts)), "day": _local_day(p["posted_at"])})

    planned = [i for i in items if i["planned_for"] and i["stage"] not in DONE_STAGES + ("dismissed",)]
    active = [i for i in items if i["stage"] in opportunities.ACTIVE_STAGES]
    grid = []
    for n in range(days):
        d = start + timedelta(days=n)
        grid.append({
            "date": d.isoformat(), "label": fmt_date(d.isoformat()), "past": d < today, "today": d == today,
            "planned": sorted((_item(i) for i in planned if _local_day(i["planned_for"]) == d), key=lambda x: x["planned_for"]),
            "posted": [{k: v for k, v in p.items() if k != "day"} for p in posts if p["day"] == d],
            # deadlines of ideas not on the calendar yet: the day to have it up by
            "due": [_item(i) for i in active if not i["planned_for"] and i["post_by"] and _local_day(i["post_by"]) == d],
        })

    warnings = []
    for g in grid:
        if len(g["planned"]) > config.CALENDAR_MAX_PER_DAY:
            warnings.append(f"{g['label']}: {len(g['planned'])} videos planned (more than {config.CALENDAR_MAX_PER_DAY} in a day)")
    empty = [g["label"] for g in grid if not g["past"] and not g["planned"] and not g["posted"]]

    weeks = defaultdict(lambda: {"formats": Counter(), "lane": 0, "music": 0})
    for kind, day, fmt, lane in ([("plan", _local_day(i["planned_for"]), i["format"], i["lane"]) for i in planned]
                                 + [("post", p["day"], p["format"], p["lane"]) for p in posts]):
        if not start <= day < end:
            continue
        w = weeks[day - timedelta(days=day.weekday())]
        w["formats"][fmt] += 1
        if lane in ("lane", "other"):
            w["music"] += 1
            w["lane"] += lane == "lane"
    balance = []
    for monday in sorted(weeks):
        w = weeks[monday]
        label = f"Week of {fmt_date(monday.isoformat())}"
        for fmt, n in w["formats"].items():
            if n > config.CALENDAR_MAX_SAME_FORMAT_WEEK:
                warnings.append(f"{label}: {n} {fmt.replace('_', ' ')} videos; mix in another format")
        if w["music"] >= 3 and w["lane"] / w["music"] < config.CALENDAR_MIN_LANE_SHARE:
            warnings.append(f"{label}: only {w['lane']} of {w['music']} music videos are in your lane")
        balance.append({"week": monday.isoformat(), "label": label, "formats": dict(w["formats"]),
                        "lane": w["lane"], "music": w["music"]})

    # The same artist twice: two plans, or a plan soon after a post about them
    recent = now - timedelta(days=config.RECENT_POST_DAYS)
    seen: dict[str, list[str]] = defaultdict(list)
    for i in sorted(planned, key=lambda i: i["planned_for"]):
        for a in _focus(i["artists"]):
            seen[a].append(f"planned {fmt_date(_local_day(i['planned_for']).isoformat())}")
    for p in posts:
        if opportunities._dt(p["posted_at"]) >= recent:
            for a in _focus(p["artists"]):
                if a in seen:
                    seen[a].insert(0, f"posted {fmt_date(p['day'].isoformat())}")
    names = {a.casefold(): a for i in planned for a in _display_artists(conn, i["alert_id"])}
    for a, uses in seen.items():
        if len(uses) > 1:
            warnings.append(f"{names.get(a, a)} is in {len(uses)} videos close together ({', '.join(uses)})")
    for i in planned:
        warnings += [f"{i['title']}: {c}" for c in i["caveats"] if c.startswith("Planned for")]

    series = []
    for name in sorted({s for s in config.SERIES_FOR_FORMAT.values()} | {i["series"] for i in items if i["series"]}
                       | {p["series"] for p in posts if p["series"]}):
        last = max((p["posted_at"] for p in posts if p["series"] == name), default=None)
        nxt = min((i["planned_for"] for i in planned if i["series"] == name and opportunities._dt(i["planned_for"]) >= now),
                  default=None)
        cadence = config.SERIES_CADENCE_DAYS.get(name)
        overdue = bool(cadence and not nxt and (last is None or opportunities._dt(last) < now - timedelta(days=cadence)))
        if overdue and last:
            warnings.append(f"{name}: last posted {fmt_date(_local_day(last).isoformat())}, nothing planned "
                            f"(usually every {cadence} days)")
        series.append({"name": name, "last_posted": last, "next_planned": nxt, "cadence_days": cadence,
                       "overdue": overdue, "ideas": sum(1 for i in active if i["series"] == name)})

    unplanned = sorted((_item(i) for i in active if not i["planned_for"]),
                       key=lambda x: (x["post_by"] is None, x["post_by"] or "", -(x["score"] or 0)))
    return {"start": start.isoformat(), "days": grid, "empty_days": empty, "warnings": warnings,
            "balance": balance, "series": series, "unplanned": unplanned,
            "default_hour": config.CALENDAR_DEFAULT_HOUR}


def _display_artists(conn, alert_id: int) -> set[str]:
    row = conn.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not row:
        return set()
    facts = json.loads(row["facts"])
    shows = facts.get("shows") or ([facts["show"]] if facts.get("show") else [])
    return {n for n in [facts.get("artist"), *(s.get("artist") for s in shows)] if n}
