"""Music stories, step 5 (docs/agent-notes/music-roadmap.md M5): a score for each story, your feedback buttons, and
Send to queue. No LLM and no web lookups: everything comes from stored stories, catalogs, events and your actions.

The score has the queue's layout (opportunities.py): components of 0-100 with a one-line reason, unknown ones left
out rather than guessed, weights in config.MUSIC_WEIGHTS. It's tuned to your M0 judgments: the kind of story and
its hook count most, your play count a little. Your buttons feed back in:

- **Interested / Not my lane** move that artist's and that kind of story's relevance (stored as your feedback on
  stories, never mixed into listening taste).
- **Already covered** only sinks the story.

Send to queue turns a story into a `music_story` alert whose facts are the story's evidence (sources, catalog
dates, Philly dates from the events data), so a draft can only say what a source said. Every action is logged in
`story_actions` for M9.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, listening, trends
from .detectors import fmt_date
from .scoring import EVENT_VIEW_SQL
from .stories import RELEASE_FAMILY

TZ = ZoneInfo(config.TIMEZONE)
FEEDBACK = ("interested", "not_my_lane", "already_covered")
BUTTON = {"interested": "Interested", "not_my_lane": "Not my lane", "already_covered": "Already covered"}

LABEL = {"content": "Content potential", "urgency": "Urgency", "relevance": "Your artists",
         "confidence": "Confidence", "search": "Search demand", "momentum": "Momentum"}

TYPE_LABEL = {
    "album_announcement": "Album announced", "album_release": "Album out", "single_release": "New song / video",
    "tracklist": "Tracklist", "tease": "Tease", "collaboration": "Collab", "tour": "Tour", "chart": "Chart",
    "brand_collab": "Brand / media", "legal": "Legal", "sensitive": "Death / health", "politics": "Politics",
    "lifestyle": "Lifestyle", "anniversary": "Anniversary", "review": "Review", "roundup": "Roundup",
    "artist_development": "Career news", "other": "Other",
}

# (value, why) per story type: your M0 yeses and nos
CONTENT = {
    "brand_collab": (90, "Brand or media tie-in: your top kind of story"),
    "tour": (75, "Tour news"),
    "roundup": (75, "A new-music roundup"),
    "legal": (65, "A case you could explain as a fan"),
    "collaboration": (65, "A collab"),
    "album_release": (60, "An album is out"),
    "album_announcement": (55, "An album announcement (it needs a hook)"),
    "anniversary": (55, "An anniversary: works as an engagement angle"),
    "artist_development": (55, "Career news"),
    "single_release": (50, "A new song or video"),
    "tracklist": (50, "A tracklist"),
    "tease": (50, "A tease"),
    "sensitive": (35, "Death or health news"),
    "review": (35, "A review"),
    "chart": (30, "Chart news: rarely anything to add"),
    "lifestyle": (15, "Lifestyle news: usually a pass"),
    "politics": (10, "Politics: you'd rather not get political"),
    "other": (30, "No clear kind of story"),
}

# A suggestion for the card, not a fact: what you said made these work
ANGLE = {
    "brand_collab": "Lead with the tie-in: the brand or the nostalgia is the hook.",
    "tour": "What to expect: setlist, crowd, openers.",
    "roundup": "Your picks from this week's new music.",
    "legal": "Explain the case as a fan; say only what the sources say.",
    "collaboration": "The pairing: who brought who in, and is it a surprise?",
    "album_release": "First listen: your take or a quick ranking.",
    "album_announcement": "Find the hook: years since the last album, a big feature, a concept, a vinyl, a label move.",
    "anniversary": "Engagement bait: is it still their best?",
    "artist_development": "What the move means for them.",
    "single_release": "First listen: your take.",
    "tracklist": "The features and what they say about the album.",
    "tease": "Read the clues: what are they hinting at?",
    "sensitive": "Handle with care: no jokes, no hooks.",
    "review": "Agree or disagree with the review.",
    "chart": "Only worth it with something to add beyond the number.",
    "lifestyle": "Usually a pass, unless it feeds a running joke about them.",
    "politics": "Only if it feeds a running joke about them; otherwise a pass.",
}

TIER_VALUE = {"core": (75, "Core watchlist artist"), "watch": (70, "On your watch list"),
              "adjacent": (55, "Adjacent to your rotation"), "breakout": (50, "Breakout watch")}


def _dt(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def _hours(td: timedelta) -> str:
    h = td.total_seconds() / 3600
    return f"{round(h)}h" if h < 48 else f"{round(h / 24)} days"


def _ids(value) -> list[int]:
    return json.loads(value or "[]") if isinstance(value, str) or value is None else list(value)


# ---------- what the score reads besides the story ----------

def context(conn, now: datetime | None = None) -> dict:
    """Everything evaluate() needs beyond the story itself, loaded once per request."""
    now = now or datetime.now(timezone.utc)
    taste = listening.load()
    artists = {r["id"]: {"id": r["id"], "name": r["name"], "tier": r["tier"], "seen_live": bool(r["seen_live"]),
                         "plays": taste.plays.get(r["key"], 0)}
               for r in conn.execute("SELECT id, name, key, tier, seen_live FROM artist_identities")}
    # Your feedback as it stands (a cleared button no longer counts), per artist and per kind of story
    by_artist: dict[int, Counter] = {}
    by_type: dict[str, Counter] = {}
    for r in conn.execute("SELECT primary_artist_id, story_type, feedback FROM stories WHERE feedback IN (?, ?)",
                          ("interested", "not_my_lane")):
        by_artist.setdefault(r["primary_artist_id"], Counter())[r["feedback"]] += 1
        by_type.setdefault(r["story_type"], Counter())[r["feedback"]] += 1
    today = now.astimezone(TZ).date().isoformat()
    shows: dict[str, list[dict]] = {}
    for e in conn.execute(EVENT_VIEW_SQL + " ORDER BY e.local_date", (today,)):
        for name in [e["headliner"] or e["name"], *(e["support"] or "").split(", ")]:
            if name:
                shows.setdefault(name.casefold(), []).append(
                    {"date": fmt_date(e["local_date"]), "venue": e["venue_name"], "url": e["url"],
                     "headliner": (e["headliner"] or e["name"]) == name})
    cutoff = (now - timedelta(days=config.RECENT_POST_DAYS)).isoformat()
    posts = []
    for p in conn.execute("SELECT alert_id, posted_at, facts_snapshot FROM posts WHERE posted_at >= ?", (cutoff,)):
        facts = json.loads(p["facts_snapshot"]) if p["facts_snapshot"] else {}
        names = {facts.get("artist")} | {s.get("artist") for s in facts.get("shows") or []} | set(
            facts.get("other_artists") or [])
        posts.append({"alert_id": p["alert_id"], "posted_at": p["posted_at"],
                      "artists": {n.casefold() for n in names if n}})
    return {"now": now, "artists": artists, "by_artist": by_artist, "by_type": by_type, "shows": shows,
            "posts": posts, "notes": trends.list_notes(conn, now)}


# ---------- components ----------

def content(story: dict, ctx: dict) -> tuple[float, str]:
    value, why = CONTENT.get(story["story_type"], CONTENT["other"])
    artist = ctx["artists"].get(story["primary_artist_id"]) or {}
    if story["story_type"] == "tour":
        if artist.get("seen_live"):
            value, why = 95, "Tour by an artist you've seen live"
        shows = ctx["shows"].get((artist.get("name") or "").casefold(), [])
        if shows:
            value, why = min(100, value + 10), f"{why}, and a Philly date ({shows[0]['date']}, {shows[0]['venue']})"
    others = [ctx["artists"][i]["name"] for i in _ids(story["other_artist_ids"]) if i in ctx["artists"]]
    if others and story["story_type"] in RELEASE_FAMILY:
        value, why = min(100, value + 10), f"{why}, with {', '.join(others[:2])}"
    return value, why


def urgency(story: dict, now: datetime) -> tuple[float | None, str]:
    """Fresh news, or a release date close by, whichever is more pressing."""
    options = []
    if story["dated"]:
        age = now - _dt(story["first_seen"])
        value = next(v for limit, v in ((12, 100), (24, 85), (48, 65), (96, 40), (1e9, 20))
                     if age.total_seconds() / 3600 <= limit)
        options.append((value, f"First reported {_hours(age)} ago"))
    if story.get("release_date") and story.get("verification") != "conflicting":
        days = (date.fromisoformat(story["release_date"]) - now.astimezone(TZ).date()).days
        if 0 <= days <= 7:
            options.append((100 if days <= 3 else 85, "Out today" if days == 0 else
                            f"Out {fmt_date(story['release_date'])} ({days} day{'s' if days > 1 else ''})"))
        elif -2 <= days < 0:
            options.append((90, f"Out since {fmt_date(story['release_date'])}"))
    if not options:
        return None, "Unknown: the feed gives no date"
    return max(options, key=lambda o: o[0])


def relevance(story: dict, ctx: dict) -> tuple[float, str]:
    artist = ctx["artists"].get(story["primary_artist_id"])
    if artist is None:
        return 40, "Not on your watchlist anymore"
    value, why = TIER_VALUE.get(artist["tier"], (40, "Dropped from your rotation"))
    if artist["plays"]:
        value += min(15, artist["plays"] / 20)
        why += f", {artist['plays']} plays"
    mine, kind = ctx["by_artist"].get(artist["id"], Counter()), ctx["by_type"].get(story["story_type"], Counter())
    # This story's own button is the card's state, not a lesson for itself
    own = story.get("feedback")
    mine_n = {k: mine[k] - (own == k) for k in ("interested", "not_my_lane")}
    kind_n = {k: kind[k] - (own == k) for k in ("interested", "not_my_lane")}
    adjust = (min(20, 10 * mine_n["interested"]) - min(50, 25 * mine_n["not_my_lane"])
              + min(15, 5 * kind_n["interested"]) - min(30, 10 * kind_n["not_my_lane"]))
    def said(counts: dict) -> str:
        return " and ".join(f"{n} {BUTTON[k]}" for k, n in counts.items() if n)
    if said(mine_n):
        why += f"; their stories you marked: {said(mine_n)}"
    if said(kind_n):
        why += f"; {TYPE_LABEL.get(story['story_type'], story['story_type'])} stories you marked: {said(kind_n)}"
    return value + adjust, why


def confidence(story: dict) -> tuple[float, str]:
    v = story.get("verification")
    if v == "conflicting":
        return 25, "Sources disagree on the date: check before posting"
    if story.get("release_status") == "rumored":
        return 35, "Reported as a rumor"
    if v == "confirmed":
        return 100, "Listed on the release catalogs" if story.get("origin") == "catalog" else "Confirmed by the catalogs"
    if v == "corroborated":
        n = story.get("source_count") or 2
        return 80, f"{n} outlets report it"
    return 50, "One source so far"


def _component(key: str, value: float | None, why: str) -> dict:
    return {"key": key, "label": LABEL[key], "weight": config.MUSIC_WEIGHTS[key],
            "value": None if value is None else round(min(100.0, max(0.0, value))), "why": why}


def _band(score: float | None) -> str | None:
    if score is None:
        return None
    return "strong" if score >= config.QUEUE_STRONG else "medium" if score >= config.QUEUE_MEDIUM else "low"


def search_facts(story: dict, ctx: dict) -> dict:
    """What trend notes are matched against: the story's artists."""
    names = [ctx["artists"][i]["name"] for i in [story["primary_artist_id"], *_ids(story["other_artist_ids"])]
             if i in ctx["artists"]]
    return {"artist": names[0] if names else None, "other_artists": names[1:]}


def evaluate(story: dict, ctx: dict) -> dict:
    """Score one story (a `stories` row as a dict; `source_count` optional)."""
    now = ctx["now"]
    facts = search_facts(story, ctx)
    notes_facts = {"artist": facts["artist"], "shows": [{"artist": n} for n in facts["other_artists"]]}
    s_value, s_why, matches = trends.search_component(list(ctx["notes"]), notes_facts, [], story["title"])
    comps = [
        _component("content", *content(story, ctx)),
        _component("urgency", *urgency(story, now)),
        _component("relevance", *relevance(story, ctx)),
        _component("confidence", *confidence(story)),
        _component("search", s_value, s_why),
        _component("momentum", None, "Unknown until artist momentum exists (roadmap M7)"),
    ]
    known = [c for c in comps if c["value"] is not None]
    weight = sum(c["weight"] for c in known)
    score = round(sum(c["weight"] * c["value"] for c in known) / weight, 1) if weight else None
    caveats = []
    if score is not None and story["story_type"] == "politics" and story.get("feedback") != "interested":
        if score > config.MUSIC_POLITICS_CAP:
            score = config.MUSIC_POLITICS_CAP
        caveats.append("Politics: held down unless you mark it Interested")
    if score is not None and story.get("verification") == "conflicting":
        score = min(score, config.MUSIC_CONFLICT_CAP)
        caveats.append("Sources disagree on the release date: check the evidence")
    if story.get("verification") == "unverified" and story.get("origin") != "catalog":
        caveats.append("Research needed: one source so far")
    if story.get("sensitive"):
        caveats.append("Sensitive (death, health or legal): handle with care; never sent automatically")
    names = {n.casefold(): n for n in [facts["artist"], *facts["other_artists"]] if n}
    for p in sorted(ctx["posts"], key=lambda p: p["posted_at"], reverse=True):
        both = names.keys() & p["artists"]
        if both and p["alert_id"] != story.get("alert_id"):
            local = _dt(p["posted_at"]).astimezone(TZ)
            caveats.append(f"You posted about {', '.join(sorted(names[n] for n in both))} "
                           f"on {fmt_date(local.date().isoformat())}")
            break
    reasons = [c["why"] for c in sorted(known, key=lambda c: c["weight"] * c["value"], reverse=True)
               if c["value"] >= config.REASON_MIN][:3]
    artist = ctx["artists"].get(story["primary_artist_id"]) or {}
    angle = ANGLE.get(story["story_type"])
    if story["story_type"] == "tour" and artist.get("seen_live"):
        angle = "You've seen them live: what the show is like, the setlist, the crowd."
    return {
        "score": score, "band": _band(score), "coverage": round(weight / sum(config.MUSIC_WEIGHTS.values()), 2),
        "components": comps, "reasons": reasons, "caveats": caveats, "angle": angle,
        "philly_shows": ctx["shows"].get((artist.get("name") or "").casefold(), [])[:3],
        "trend_notes": matches, "rules_version": config.MUSIC_RULES_VERSION,
    }


def rank_key(story: dict) -> tuple:
    """For you: stories you haven't handled first, best score first."""
    return (story.get("feedback") in ("not_my_lane", "already_covered"), story.get("alert_id") is not None,
            -(story.get("score") or 0))


# ---------- your actions ----------

class MusicError(ValueError):
    pass


def _story(conn, story_id: int) -> dict:
    row = conn.execute("""SELECT s.*, (SELECT COUNT(DISTINCT a.source_id) FROM story_articles x
                                       JOIN articles a ON a.id = x.article_id WHERE x.story_id = s.id) AS source_count
                          FROM stories s WHERE s.id=?""", (story_id,)).fetchone()
    if row is None:
        raise KeyError(story_id)
    return dict(row)


def _log(conn, story: dict, action: str, score: float | None, now: datetime) -> None:
    conn.execute("""INSERT INTO story_actions (story_id, story_key, artist_id, story_type, action, score, at)
                    VALUES (?,?,?,?,?,?,?)""", (story["id"], story["key"], story["primary_artist_id"],
                                                story["story_type"], action, score,
                                                now.replace(microsecond=0).isoformat()))


def set_feedback(conn, story_id: int, action: str | None, now: datetime | None = None) -> dict:
    """Interested, Not my lane, Already covered, or None to clear. Logged either way."""
    if action not in (*FEEDBACK, None):
        raise MusicError(f"feedback must be one of {', '.join(FEEDBACK)}, or null")
    now = now or datetime.now(timezone.utc)
    story = _story(conn, story_id)
    score = evaluate(story, context(conn, now))["score"]
    conn.execute("UPDATE stories SET feedback=? WHERE id=?", (action, story_id))
    _log(conn, story, action or "cleared", score, now)
    conn.commit()
    return {**story, "feedback": action}


def facts_for(conn, story: dict, ctx: dict) -> dict:
    """The alert's facts: only what a source, a catalog or the events data says, each with where it came from."""
    artist = ctx["artists"].get(story["primary_artist_id"]) or {}
    sources = [{"outlet": r["source_name"], "headline": r["title"], "url": r["url"], "date": r["seen_at"][:10]}
               for r in conn.execute("""SELECT a.title, a.url, s.name AS source_name,
                                               COALESCE(a.published_at, a.fetched_at) AS seen_at
                                        FROM story_articles x JOIN articles a ON a.id = x.article_id
                                        JOIN news_sources s ON s.id = a.source_id
                                        WHERE x.story_id=? ORDER BY seen_at""", (story["id"],))]
    evidence = [dict(e) for e in conn.execute("SELECT * FROM story_evidence WHERE story_id=? ORDER BY observed_at",
                                              (story["id"],))]
    facts = {
        "format": "music_story", "story_id": story["id"], "story_type": story["story_type"],
        "artist": artist.get("name"),
        "other_artists": [ctx["artists"][i]["name"] for i in _ids(story["other_artist_ids"]) if i in ctx["artists"]],
        "headline": story["title"], "sources": sources, "verification": story["verification"],
        "sensitive": bool(story["sensitive"]), "your_plays": artist.get("plays") or 0,
    }
    if story["release_title"]:
        facts["release_title"] = story["release_title"]
    if story["release_status"]:
        facts["release_status"] = story["release_status"]
    dated = [e for e in evidence if e["claim"] == "release_date"]
    if dated and story["verification"] != "conflicting":
        facts["release_date"] = fmt_date(story["release_date"]) if story["release_date"] else None
        facts["release_date_from"] = sorted({e["provider"] for e in dated if e["value"] == story["release_date"]})
    elif dated:  # sources disagree: the draft gets every stated date with its source, and no single date
        facts["release_dates_reported"] = [{"date": fmt_date(e["value"]), "from": e["provider"]} for e in dated]
    shows = ctx["shows"].get((artist.get("name") or "").casefold(), [])
    if shows:
        facts["philly_shows"] = shows[:5]
    return {k: v for k, v in facts.items() if v is not None}


def _timing(story: dict, now: datetime) -> tuple[str, str]:
    """post_by / expires_at for the alert: a day after the news (or by release day), stale a week later."""
    first = _dt(story["first_seen"]) if story["dated"] else now
    post_by, expires = first + timedelta(hours=config.NEWS_FRESH_HOURS), first + timedelta(days=config.NEWS_EXPIRES_DAYS)
    if story["release_date"] and story["verification"] != "conflicting":
        drop = datetime.combine(date.fromisoformat(story["release_date"]), datetime.min.time(), TZ)
        if drop > now:
            post_by, expires = drop, drop + timedelta(days=config.NEWS_EXPIRES_DAYS)
    return post_by.astimezone(timezone.utc).isoformat(), expires.astimezone(timezone.utc).isoformat()


def send(conn, story_id: int, now: datetime | None = None, auto: bool = False) -> int:
    """Send to queue: a `music_story` alert from the story's evidence. Returns the alert id (the same one again
    if it was already sent)."""
    from .detectors import save_alerts
    now = now or datetime.now(timezone.utc)
    story = _story(conn, story_id)
    if story["alert_id"] and conn.execute("SELECT 1 FROM alerts WHERE id=?", (story["alert_id"],)).fetchone():
        return story["alert_id"]
    ctx = context(conn, now)
    scored = evaluate(story, ctx)
    post_by, expires = _timing(story, now)
    key = f"music:{story['key']}"
    save_alerts(conn, [{"key": key, "kind": "music_story", "title": story["title"], "score": scored["score"] or 0,
                        "facts": facts_for(conn, story, ctx), "post_by": post_by, "expires_at": expires}])
    alert_id = conn.execute("SELECT id FROM alerts WHERE key=?", (key,)).fetchone()["id"]
    conn.execute("UPDATE stories SET alert_id=? WHERE id=?", (alert_id, story_id))
    _log(conn, story, "auto_sent" if auto else "sent", scored["score"], now)
    conn.commit()
    return alert_id


def auto_send(conn, now: datetime | None = None) -> list[int]:
    """config.MUSIC_AUTO_SEND (off until M9): send fresh stories that clear the score and confidence bars."""
    if not config.MUSIC_AUTO_SEND:
        return []
    now = now or datetime.now(timezone.utc)
    ctx = context(conn, now)
    since = (now - timedelta(days=2)).replace(microsecond=0).isoformat()
    sent = []
    for r in conn.execute("""SELECT id FROM stories WHERE last_seen >= ? AND alert_id IS NULL AND feedback IS NULL
                             AND sensitive = 0 AND verification IN ('corroborated', 'confirmed')""", (since,)).fetchall():
        story = _story(conn, r["id"])
        if (evaluate(story, ctx)["score"] or 0) >= config.MUSIC_AUTO_SEND_MIN:
            sent.append(send(conn, r["id"], now, auto=True))
    return sent
