"""Editorial calendar (roadmap P1-13): what's planned, posted and due over the next two weeks.

A nice-to-have view of its own (the Calendar tab); the other tabs don't depend on it. A plan is two fields you
set on an idea: `planned_for` (when you might post it) and `series` (which recurring series it belongs to; the
format's default from config.SERIES_FOR_FORMAT when unset). A plan is a shortlist for that day, not a commitment:
plan as many as you like and pick on the day. Nothing posts at the planned time and nothing here changes an idea,
a draft or a logged post. The calendar is built on every request from the queue (opportunities.queue) and the
posts table.

It doesn't nag about how much is planned. The only per-plan note is a factual one: a plan that falls after the
idea goes stale (a presale alarm planned after tickets went on sale) or after its post-by time.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, opportunities
from .detectors import fmt_date

TZ = ZoneInfo(config.TIMEZONE)
DONE_STAGES = ("posted", "measured")


def _local_day(iso: str) -> date:
    return opportunities._dt(iso).astimezone(TZ).date()


def late_note(item: dict) -> str | None:
    """'after it goes stale' / 'after its post-by time' when the plan comes too late, else None."""
    planned = opportunities._dt(item["planned_for"])
    if item["expires_at"] and planned > opportunities._dt(item["expires_at"]):
        return "after it goes stale"
    if item["post_by"] and planned > opportunities._dt(item["post_by"]):
        return "after its post-by time"
    return None


def _item(i: dict) -> dict:
    out = {k: i[k] for k in ("alert_id", "kind", "format", "title", "stage", "band", "score", "post_by",
                             "expires_at", "planned_for", "series", "series_set", "lane")}
    out["late"] = late_note(i) if i["planned_for"] else None
    return out


def calendar(conn, now: datetime | None = None, start: date | None = None, days: int = config.CALENDAR_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(TZ).date()
    start = start or today - timedelta(days=today.weekday())  # Monday of this week
    end = start + timedelta(days=days)
    items = opportunities.queue(conn, now)
    by_id = {i["alert_id"]: i for i in items}

    posts = []
    for p in conn.execute("SELECT * FROM posts ORDER BY posted_at"):
        alert = by_id.get(p["alert_id"])
        posts.append({"id": p["id"], "alert_id": p["alert_id"], "title": p["title"], "posted_at": p["posted_at"],
                      "url": p["url"], "format": p["format"],
                      "series": p["series"] or config.SERIES_FOR_FORMAT.get(p["format"] or ""),
                      "lane": alert["lane"] if alert else None, "day": _local_day(p["posted_at"])})

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

    # What the two weeks hold, for reference only (no targets)
    weeks = defaultdict(lambda: {"formats": Counter(), "lane": 0, "music": 0})
    for day, fmt, lane in ([(_local_day(i["planned_for"]), i["format"], i["lane"]) for i in planned]
                           + [(p["day"], p["format"], p["lane"]) for p in posts]):
        if not start <= day < end:
            continue
        w = weeks[day - timedelta(days=day.weekday())]
        w["formats"][fmt] += 1
        if lane in ("lane", "other", "music"):  # "music": a music-news story, about a watchlist artist
            w["music"] += 1
            w["lane"] += lane in ("lane", "music")
    mix = [{"week": m.isoformat(), "label": f"Week of {fmt_date(m.isoformat())}", "formats": dict(weeks[m]["formats"]),
            "lane": weeks[m]["lane"], "music": weeks[m]["music"]} for m in sorted(weeks)]

    series = []
    for name in sorted(set(config.SERIES_FOR_FORMAT.values()) | {i["series"] for i in items if i["series"]}
                       | {p["series"] for p in posts if p["series"]}):
        last = max((p["posted_at"] for p in posts if p["series"] == name), default=None)
        nxt = min((i["planned_for"] for i in planned if i["series"] == name and opportunities._dt(i["planned_for"]) >= now),
                  default=None)
        cadence = config.SERIES_CADENCE_DAYS.get(name)
        # only once it has run at least once: a series you've never posted isn't "behind"
        behind = bool(cadence and last and not nxt and opportunities._dt(last) < now - timedelta(days=cadence))
        series.append({"name": name, "last_posted": last, "next_planned": nxt, "cadence_days": cadence,
                       "behind": behind, "ideas": sum(1 for i in active if i["series"] == name)})

    unplanned = sorted((_item(i) for i in active if not i["planned_for"]),
                       key=lambda x: (x["post_by"] is None, x["post_by"] or "", -(x["score"] or 0)))
    return {"start": start.isoformat(), "days": grid, "mix": mix, "series": series, "unplanned": unplanned,
            "default_hour": config.CALENDAR_DEFAULT_HOUR}
