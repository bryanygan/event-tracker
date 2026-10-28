"""Content queue: which video ideas are worth making *now*, and why.

Every alert is a video idea. Its notability score (scoring.py) says how big the artist/show is; this
module adds a separate, explainable **opportunity score** for "is this a good video right now?":

    audience  (notability)            timing (fresh news, deadline coming up)
    story     (comeback, big name in a small room, a packed month)
    local     (Philly, in your lane)  search (your trend notes, trends.py)   ready (facts complete, draft clean)

Each component is 0-100 with a one-line reason, or None when we have no data. Unknown components are left
out and their weight is shared among the known ones; `coverage` says how much of the weight was known.
The weights (config.OPPORTUNITY_WEIGHTS) are guesses to tune against logged posts, not a virality forecast.

Everything here is computed from stored data on each request, so nothing can drift out of date. The only
state is yours: shortlisted, snoozed_until and note on the alert.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, music, trends
from .detectors import fmt_date
from .drafts import draft_row
from .scoring import upcoming_events

TZ = ZoneInfo(config.TIMEZONE)
NEWS_KINDS = ("new_tour", "status_change")  # timing comes from when we noticed them, not a calendar date

COMPONENT_LABEL = {"audience": "Audience", "timing": "Timing", "story": "Story", "local": "Philly + your lane",
                   "search": "Search demand", "ready": "Ready to make"}


def _dt(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def _when(dt: datetime) -> str:
    local = dt.astimezone(TZ)
    return f"{fmt_date(local.date().isoformat())} {local.strftime('%I:%M %p').lstrip('0')}"


def _hours(td: timedelta) -> str:
    h = td.total_seconds() / 3600
    return f"{round(h)}h" if h < 48 else f"{round(h / 24)} days"


def timing(alert: dict, now: datetime) -> dict:
    """post_by / expires_at plus a 0-100 timing component. News is judged by age; the rest by deadline."""
    created = _dt(alert["created_at"])
    post_by, expires = _dt(alert["post_by"]), _dt(alert["expires_at"])
    if alert["kind"] in NEWS_KINDS:
        post_by = created + timedelta(hours=config.NEWS_FRESH_HOURS)
        news_end = created + timedelta(days=config.NEWS_EXPIRES_DAYS)
        expires = min(expires, news_end) if expires else news_end
    out = {"post_by": post_by.isoformat() if post_by else None, "expires_at": expires.isoformat() if expires else None,
           "expired": bool(expires and now > expires)}
    if out["expired"]:
        return {**out, "value": 0, "why": f"Expired {_when(expires)}"}
    if alert["kind"] in NEWS_KINDS:
        age = now - created
        value = next(v for limit, v in ((12, 100), (24, 85), (48, 65), (96, 40), (1e9, 20)) if age.total_seconds() / 3600 <= limit)
        return {**out, "value": value, "why": f"Noticed {_hours(age)} ago"}
    if post_by is None:  # raised before timing was recorded and not re-detected since
        return {**out, "value": None, "why": "No deadline recorded for this older alert"}
    left = post_by - now
    if left.total_seconds() < 0:
        return {**out, "value": 30, "why": f"Past the best time to post ({_when(post_by)}); still useful until {_when(expires)}"
                if expires else f"Past the best time to post ({_when(post_by)})"}
    hours_left = left.total_seconds() / 3600
    value = next(v for limit, v in ((48, 100), (96, 80), (14 * 24, 55), (1e9, 35)) if hours_left <= limit)
    return {**out, "value": value, "why": f"Best posted by {_when(post_by)} ({_hours(left)} left)"}


def _component(key: str, value: float | None, why: str) -> dict:
    return {"key": key, "label": COMPONENT_LABEL[key], "weight": config.OPPORTUNITY_WEIGHTS[key],
            "value": None if value is None else round(min(100.0, max(0.0, value))), "why": why}


LOCAL_KINDS = ("experience", "things_to_do")  # markets, festivals, comedy (P2-14)


def story(alert: dict, facts: dict, events: list[dict]) -> tuple[float, str]:
    """The angle beyond 'X is playing': the strongest applicable rule wins."""
    kind = alert["kind"]
    options = {
        "new_tour": (70, "New Philly date (nothing else announced here)") if not facts.get("already_had_philly_dates")
        else (40, "An added date; they already had a Philly show"),
        "presale_soon": (30, "A ticket reminder: useful, not news"),
        "weekend": (45, f"Weekend cut for your lane ({facts['lane']})") if facts.get("lane")
        else (35, "Recurring weekend roundup"),
        "stacked_month": (75 if (facts.get("notable_show_count") or 0) >= 8 else 55,
                          f"{facts.get('notable_show_count')} notable {facts.get('genre')} shows in {facts.get('month')}"),
        "status_change": (70 if facts.get("change") in ("cancelled", "postponed") else 55, f"Show {facts.get('change')}"),
        "team_week": (20, "Routine home-game week"),
        "big_game": (55, f"Marquee opponent: {facts.get('opponent')}")
        if facts.get("opponent") in (config.SPORTS_TEAMS.get(facts.get("team")) or {}).get("marquee", ())
        else (45, f"{facts.get('artist')} home game"),
        "experience": (60, f"Local {(facts.get('category') or 'event').lower()}: not a concert, a wider audience"),
        "things_to_do": (45, "Weekend 'things to do' roundup"),
    }
    best = [options.get(kind, (30, "No special angle"))]
    history = facts.get("history") or {}
    if history.get("is_comeback"):
        if history.get("last_philly_show_was_multi_act"):
            best.append((75, f"First Philly show of their own in a while; last time ({history['last_philly_show_year']}) "
                             f"was a {history['last_philly_show_lineup_size']}-act lineup"))
        else:
            best.append((90, f"Back in Philly after {history['years_since_last_philly_show']} years"))
    for e in events:  # a name that could fill a bigger room playing a small one
        if (e.get("popularity") or 0) >= 65 and (e.get("venue_tier") or 100) <= 35:
            best.append((80, f"Big name (popularity {e['popularity']}) in a small room ({e['venue_name']})"))
            break
    return max(best, key=lambda x: x[0])


def local(alert: dict, facts: dict, events: list[dict]) -> tuple[float, str]:
    if alert["kind"] in ("team_week", "big_game"):
        return 100, f"{facts.get('artist')} home game"
    if alert["kind"] in LOCAL_KINDS:
        return 100, "Happening in Philly"
    plays = max([e.get("my_plays") or 0 for e in events] + [facts.get("your_plays") or 0])
    if plays >= config.ROTATION_MIN_PLAYS:
        return 100, f"Philly show, in your rotation ({plays} plays)"
    lane = next((e.get("genre") or e.get("my_bucket") for e in events if e.get("niche")), None)
    if lane or (alert["kind"] == "stacked_month" and facts.get("genre") in config.NICHE_TM_GENRES):
        return 100, f"Philly show in your lane ({lane or facts.get('genre')})"
    return 70, "Philly-area show, outside your usual genres"


def ready(facts: dict, draft: dict | None) -> tuple[float, str]:
    shows = facts.get("shows") or ([facts["show"]] if facts.get("show") else [])
    have = [sum(bool(s.get(k)) for k in ("url", "time", "venue")) / 3 for s in shows]
    complete = sum(have) / len(have) if have else 1.0
    if draft is None:
        d, why = 0, "no draft yet"
    elif draft["facts_changes"]:
        d, why = 30, "draft is out of date"
    elif draft["status"] in ("approved", "filmed"):
        d, why = 100, f"draft {draft['status']}"
    elif draft["status"] == "rejected":
        d, why = 0, "draft rejected"
    else:
        d, why = (40, "draft has warnings") if draft["warnings"] else (70, "draft ready to review")
    return 50 * complete + 0.5 * d, f"{round(complete * 100)}% of show details known (ticket link, time, venue); {why}"


def _match_shows(facts: dict, events) -> list[dict]:
    """Alerts raised before event_ids were stored: find their events by artist + date instead."""
    shows = facts.get("shows") or ([facts["show"]] if facts.get("show") else [])
    wanted = {((s.get("artist") or "").casefold(), s.get("date")) for s in shows}
    urls = {s["url"] for s in shows if s.get("url")}  # games: facts say "Sixers vs. Bucks", the event says the team
    return [e for e in events
            if ((e["headliner"] or "").casefold(), fmt_date(e["local_date"])) in wanted or e.get("url") in urls]


def _artists(facts: dict) -> set[str]:
    shows = facts.get("shows") or ([facts["show"]] if facts.get("show") else [])
    names = {facts.get("artist")} | {s.get("artist") for s in shows}
    return {n.casefold() for n in names if n}


def series_for(alert: dict) -> str | None:
    """The idea's series: yours if you set one, else the format's default (config.SERIES_FOR_FORMAT)."""
    facts = alert["facts"] if isinstance(alert["facts"], dict) else json.loads(alert["facts"])
    return alert.get("series") or config.SERIES_FOR_FORMAT.get(facts.get("format") or alert["kind"])


def stage(alert: dict, draft: dict | None, posts: list[dict], expired: bool, now: datetime) -> str:
    """Where the idea is in your workflow, derived from alert/draft/post state (nothing extra stored)."""
    if alert["status"] == "dismissed":
        return "dismissed"
    if posts:
        return "measured" if any(p["has_metrics"] for p in posts) else "posted"
    if expired:
        return "expired"
    if alert["snoozed_until"] and _dt(alert["snoozed_until"]) > now:
        return "snoozed"
    if draft:
        if draft["facts_changes"]:
            return "needs_refresh"
        if draft["status"] in ("approved", "filmed"):
            return draft["status"]
        if draft["status"] == "pending":
            return "review"
    return "shortlisted" if alert["shortlisted"] else "new"


def evaluate(alert: dict, events: list[dict], draft: dict | None, posts: list[dict],
             recent_posts: list[dict], now: datetime, notes: list[dict] = ()) -> dict:
    """Score one idea. `alert` is a DB row as a dict, `events` the upcoming events it links to,
    `draft` its latest draft (with facts_changes/warnings parsed), `posts` posts logged for it and
    `recent_posts` all posts in the last RECENT_POST_DAYS (for the 'already covered' warning) and `notes`
    the trend notes (trends.list_notes) to match against it."""
    facts = alert["facts"]
    t = timing(alert, now)
    search_value, search_why, trend_matches = trends.search_component(list(notes), facts, events, alert.get("title") or "")
    comps = [
        _component("audience", min(100.0, alert["score"]), f"Notability {round(alert['score'])} (artist size, venue, tour, your taste)"),
        _component("timing", t["value"], t["why"]),
        _component("story", *story(alert, facts, events)),
        _component("local", *local(alert, facts, events)),
        _component("search", search_value, search_why),
        _component("ready", *ready(facts, draft)),
    ]
    known = [c for c in comps if c["value"] is not None]
    weight = sum(c["weight"] for c in known)
    score = round(sum(c["weight"] * c["value"] for c in known) / weight, 1) if weight else None

    reasons = [c["why"] for c in sorted(known, key=lambda c: c["weight"] * c["value"], reverse=True)
               if c["value"] >= config.REASON_MIN and c["key"] != "ready"][:3]
    # Search demand is unknown for every idea until there's a data source; the queue header says so once.
    caveats = [f"{c['label']}: {c['why']}" for c in comps if c["value"] is None and c["key"] != "search"]
    if draft and draft["facts_changes"]:
        caveats.append(f"Facts changed since the draft: {'; '.join(draft['facts_changes'][:3])}")
    if draft and draft["warnings"]:
        caveats.append(f"Draft has {len(draft['warnings'])} thing(s) to check before filming")
    if (facts.get("history") or {}).get("is_comeback"):
        caveats.append("The comeback angle comes from setlist.fm, which is crowd-sourced: verify it")
    mine = _artists(facts)
    for p in recent_posts:
        if p["alert_id"] != alert["id"] and mine & p["artists"]:
            caveats.append(f"You posted about {', '.join(sorted(mine & p['artists']))} on {_when(_dt(p['posted_at']))}")
            break
    if not events:
        caveats.append("None of its shows are listed anymore (past, or removed from the feed)")

    band = None if score is None else "strong" if score >= config.QUEUE_STRONG else "medium" if score >= config.QUEUE_MEDIUM else "low"
    return {
        "score": score, "band": band, "coverage": round(weight / sum(config.OPPORTUNITY_WEIGHTS.values()), 2),
        "components": comps, "reasons": reasons, "caveats": caveats,
        "post_by": t["post_by"], "expires_at": t["expires_at"],
        "stage": stage(alert, draft, posts, t["expired"] or not events, now),
        "rules_version": config.OPPORTUNITY_RULES_VERSION,
        "trend_notes": trend_matches,
        "lane": "sports" if alert["kind"] in ("team_week", "big_game") else "local" if alert["kind"] in LOCAL_KINDS
        else "lane" if local(alert, facts, events)[0] == 100 else "other",
    }


def evaluate_music(alert: dict, story: dict | None, ctx: dict, draft: dict | None, posts: list[dict],
                   now: datetime) -> dict:
    """A music story sent to the queue (music.py): scored like the story, with the alert's timing and stage."""
    expires = _dt(alert["expires_at"])
    expired = bool(expires and now > expires)
    if alert["kind"] != "music_story":  # a catalog roundup (music_formats.py): its own score and reasons
        from .music_formats import roundup_evaluation
        opp = {"score": alert["score"], "band": music._band(alert["score"]), "coverage": 1.0, "components": [],
               "trend_notes": [], **roundup_evaluation(alert)}
    elif story is None:  # stories with an alert are kept, so only a deleted database row gets here
        opp = {"score": alert["score"], "band": music._band(alert["score"]), "coverage": 0.0, "components": [],
               "reasons": [], "caveats": ["The story behind this idea is gone; the score is from when it was sent"],
               "trend_notes": []}
    else:
        opp = music.evaluate({**story, "alert_id": alert["id"]}, ctx)
    caveats = list(opp["caveats"])
    if draft and draft["facts_changes"]:
        caveats.append(f"Facts changed since the draft: {'; '.join(draft['facts_changes'][:3])}")
    return {
        "score": opp["score"], "band": opp["band"], "coverage": opp["coverage"], "components": opp["components"],
        "reasons": opp["reasons"], "caveats": caveats, "post_by": alert["post_by"], "expires_at": alert["expires_at"],
        "stage": stage(alert, draft, posts, expired, now), "rules_version": config.MUSIC_RULES_VERSION,
        "trend_notes": opp["trend_notes"], "lane": "music",
    }


def evaluate_vinyl(alert: dict, draft: dict | None, posts: list[dict], now: datetime) -> dict:
    """A vinyl idea (vinyl.py): evergreen, so its score is the format's base plus timing for anniversaries;
    the reasons are the idea's own."""
    expires = _dt(alert["expires_at"])
    expired = bool(expires and now > expires)
    score = alert["score"]
    caveats = []
    if not draft:
        caveats.append("Drafted on request: a researched story costs a few cents, so it isn't written automatically")
    elif draft["warnings"]:
        caveats.append(f"{len(draft['warnings'])} claim(s) to check against the sources")
    return {"score": score, "band": music._band(score), "coverage": 1.0, "components": [],
            "reasons": list(alert["facts"].get("why") or []), "caveats": caveats, "post_by": alert["post_by"],
            "expires_at": alert["expires_at"], "stage": stage(alert, draft, posts, expired, now),
            "rules_version": 1, "trend_notes": [], "lane": "vinyl"}


ACTIVE_STAGES = ("new", "shortlisted", "review", "needs_refresh", "approved", "filmed")


def queue(conn, now: datetime | None = None, alert_id: int | None = None) -> list[dict]:
    """Every alert with its opportunity evaluation, best first (shortlisted ones on top)."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(TZ).date().isoformat()
    events = {e["id"]: e for e in upcoming_events(conn, today)}
    rows = conn.execute("SELECT * FROM alerts" + (" WHERE id=?" if alert_id else ""), (alert_id,) if alert_id else ())
    alerts = [dict(r) for r in rows]

    posts_for: dict[int, list[dict]] = {}
    recent = []
    cutoff = now - timedelta(days=config.RECENT_POST_DAYS)
    for p in conn.execute("""SELECT p.*, EXISTS(SELECT 1 FROM post_metrics m WHERE m.post_id = p.id) AS has_metrics
                             FROM posts p ORDER BY posted_at"""):
        p = dict(p)
        if p["alert_id"]:
            posts_for.setdefault(p["alert_id"], []).append(p)
        if _dt(p["posted_at"]) >= cutoff:
            recent.append({**p, "artists": _artists(json.loads(p["facts_snapshot"])) if p["facts_snapshot"] else set()})

    notes = trends.list_notes(conn, now)
    music_ctx = music.context(conn, now) if any(a["kind"] in config.MUSIC_KINDS for a in alerts) else None
    out = []
    for a in alerts:
        a["facts"] = json.loads(a["facts"])
        latest = conn.execute("SELECT * FROM drafts WHERE alert_id=? ORDER BY id DESC LIMIT 1", (a["id"],)).fetchone()
        draft = draft_row(latest, a["facts"], conn) if latest else None
        shows = a["facts"].get("shows") or ([a["facts"]["show"]] if a["facts"].get("show") else [])
        links = [{"label": f"{s.get('artist') or s.get('event_name')} · {s.get('date')}", "url": s["url"]}
                 for s in shows if s.get("url")]
        if a["kind"] == config.VINYL_KIND:
            opp = evaluate_vinyl(a, draft, posts_for.get(a["id"], []), now)
            recs = [a["facts"]["record"]] if a["facts"].get("record") else a["facts"].get("records") or []
            links = [{"label": f"{r['artist']} · {r['title']}", "url": r["discogs_url"]} for r in recs
                     if r.get("discogs_url")]
        elif a["kind"] in config.MUSIC_KINDS:
            story = None
            if a["kind"] == "music_story":
                try:
                    story = music._story(conn, a["facts"]["story_id"])
                except KeyError:
                    pass
            opp = evaluate_music(a, story, music_ctx, draft, posts_for.get(a["id"], []), now)
            links = [{"label": s["outlet"], "url": s["url"]} for s in a["facts"].get("sources", []) if s.get("url")]
            links += [{"label": f"{i['artist']} · {i.get('title', '')}", "url": i["url"]}
                      for i in a["facts"].get("releases") or a["facts"].get("albums") or [] if i.get("url")]
        else:
            ids = json.loads(a["event_ids"]) if a["event_ids"] else []
            linked = [events[i] for i in ids if i in events] if ids else _match_shows(a["facts"], events.values())
            opp = evaluate(a, linked, draft, posts_for.get(a["id"], []), recent, now, notes)
        out.append({
            "alert_id": a["id"], "kind": a["kind"], "title": a["title"], "notability": a["score"],
            "created_at": a["created_at"], "updated_at": a["updated_at"],
            "shortlisted": bool(a["shortlisted"]), "snoozed_until": a["snoozed_until"], "note": a["note"],
            "planned_for": a["planned_for"], "series": series_for(a), "series_set": a["series"],
            "format": a["facts"].get("format") or a["kind"],  # planned_for/series/format: the Calendar tab
            "your_plays": a["facts"].get("your_plays") or 0,
            "links": links[:6],
            "pillar": "music" if a["kind"] in config.MUSIC_KINDS else "vinyl" if a["kind"] == config.VINYL_KIND
                      else "events",
            "draft": None if draft is None else {k: draft[k] for k in ("id", "status", "hook", "warnings", "facts_changes")},
            "posts": [{"id": p["id"], "url": p["url"], "posted_at": p["posted_at"]} for p in posts_for.get(a["id"], [])],
            **opp,
        })
    out.sort(key=lambda o: (o["stage"] not in ACTIVE_STAGES, not o["shortlisted"], -(o["score"] or 0)))
    return out


def snapshot(conn, alert_id: int) -> dict | None:
    """The evaluation as it stands right now, saved with a post (what the queue knew when you chose it)."""
    [item] = queue(conn, alert_id=alert_id) or [None]
    if item is None:
        return None
    return {k: item[k] for k in ("score", "band", "coverage", "components", "reasons", "caveats", "stage",
                                 "post_by", "rules_version", "notability", "trend_notes")}
