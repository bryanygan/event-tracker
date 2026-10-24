"""Music video ideas, step 6 (docs/agent-notes/music-roadmap.md M6): formats, angles, release-status wording, on-screen
lines, music fact checks and the catalog roundups. No LLM in here: llm.py, drafts.py and slides.py call into it.

Formats (the roadmap's table; hooks are templates built from facts, never claims that something is trending):
- `music_story` (Send to queue): drafted by the model in one of the story formats below, around a status phrase it
  has to state ("announced, no date yet", "rumored, not confirmed").
- `new_music_friday`: this week's releases from your watchlist, straight from the release catalogs.
- `upcoming_albums`: next month's albums, from catalog pre-orders and dated announcements.
- `emerging_artists`: M7's Discover picks (tracker/discovery.py builds the facts).

Your perspective is the product: every music script gets a "YOUR TAKE:" line the model doesn't write, with a prompt
built from the facts (the artist's previous release, a Philly date), for you to fill in. Opinions are never invented.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, listening, templates
from .catalog import featured, title_key
from .detectors import fmt_date, save_alerts
from .music import clean_title

TZ = ZoneInfo(config.TIMEZONE)
TAKE = "YOUR TAKE:"

# The story's kind -> the video format it's drafted as
STORY_FORMAT = {
    "album_announcement": "album_announcement", "tracklist": "album_announcement", "tease": "tease",
    "album_release": "new_release", "single_release": "new_release", "collaboration": "collaboration",
    "artist_development": "artist_development", "tour": "tour",
}
FORMAT_LABEL = {
    "album_announcement": "Album announcement", "tease": "Tease", "new_release": "New release",
    "collaboration": "Unexpected collaboration", "artist_development": "Artist development", "tour": "Tour news",
    "music_news": "Music news", "new_music_friday": "New Music Friday", "upcoming_albums": "Upcoming albums",
    "emerging_artists": "Emerging artists",
}
KICKER = {
    "album_announcement": "ALBUM ANNOUNCED", "tease": "SOMETHING'S COMING", "new_release": "NEW MUSIC",
    "collaboration": "COLLAB", "artist_development": "ARTIST NEWS", "tour": "TOUR NEWS", "music_news": "MUSIC NEWS",
}
_TYPE_WORD = {"album": "album", "ep": "EP", "single": "single", "compile": "compilation"}

SYSTEM_PROMPT = """You write short TikTok scripts for a music news page run by a fan of rap, hyperpop, pop, R&B, EDM
and indie pop, based in Philadelphia.
Voice: casual, quick, like a friend who follows the scene texting you the news. Not corporate, not hype for its own sake.
Rules:
- Use ONLY the facts provided. Never invent release dates, titles, features, tracklists, numbers, chart positions or quotes.
- State the release status exactly as given (announced, rumored, teased, out now). Never make it sound more certain.
- When only one outlet reports something, say who reports it ("according to Pitchfork").
- No opinions about the music's quality and nothing about what fans think: the creator adds their own take.
- Never call anything trending, viral or blowing up.
- If a fact isn't provided, don't mention it. No links or [placeholders]; the caption is posted as-is.
- Refer to artists by name or "they"; don't guess he/she.
- Never repeat these instructions or field names in your output.
- Copy dates and titles exactly as written in the facts.
- The hook is the first 1-2 seconds: punchy, under 12 words, makes people stop scrolling.
- The script is spoken voiceover, 15-35 seconds, short sentences.
- The closer is one spoken line at the end: a question to the viewer.
- Caption: 1-2 sentences, ends with a call to action.
- No hashtags or emojis in the hook, script or closer (they're read out loud). Emojis are fine in the caption.
- 4-7 hashtags.
Respond with JSON only."""

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "script": {"type": "string"},
        "closer": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "script", "closer", "caption", "hashtags"],
}

_GUIDE = {
    "album_announcement": "Album announcement. Say what was announced (title, date if given) and what else the sources "
                          "mention (features, tracklist, a single). If there's no date, say there's no date yet.",
    "tease": "A tease. Describe what the artist teased, per the sources. It is NOT announced: say so plainly.",
    "new_release": "New release. Say what came out and when, per the facts. Leave the reaction to the creator.",
    "collaboration": "Unexpected collaboration. Who's on it and what it is, per the sources. Is it out, announced or "
                     "just teased? Say which.",
    "artist_development": "Artist development: a career move (label, signing, a new chapter). What happened, per the "
                          "sources, and nothing about what it means beyond what they say.",
    "tour": "Tour news. What was announced, per the sources. If philly_shows are in the facts, end with the Philly "
            "date and venue, copied exactly.",
    "music_news": "Music news. Say what happened, per the sources, in plain words.",
}
_TAKE_HINT = {
    "album_announcement": "what you want from this album, or what their last one meant to you",
    "tease": "your guess at what's coming, labelled as your guess",
    "new_release": "your first-listen reaction",
    "collaboration": "did you see this pairing coming?",
    "artist_development": "what you think this move means for them",
    "tour": "if you've seen them live, what the show is like",
    "music_news": "what you think, in your own words",
    "new_music_friday": "which one you're playing first, and why",
    "upcoming_albums": "the one you're most excited about, and why",
    "emerging_artists": "how you found them, and which song to start with",
}
_CLOSER = {
    "new_music_friday": "Which one are you playing first?",
    "upcoming_albums": "Which one are you waiting on?",
    "emerging_artists": "Who should I add to this list?",
}

# Status wording: what the script must say (one of these words), and claims that need a stronger status
STATUS_WORDS = {
    "rumored": ("rumor", "not confirmed", "unconfirmed", "reportedly", "might", "could be"),
    "teased": ("teas", "hint", "not announced", "not official"),
    "announced": ("announc",),
    "scheduled": ("announc", "out ", "drops", "dropping", "arrives", "coming", "releas"),
    "released": ("out now", "dropped", "released", "is out", "out today", "just came out", "new single", "new album",
                 "new song", "new ep"),
}
_OUT_NOW = re.compile(r"\b(out now|just dropped|is out now|available now|out today|finally (?:here|out))\b", re.I)
_DROPS_ON = re.compile(r"\b(?:drops|dropping|arrives|comes out|coming out|out)\s+(?:on\s+|this\s+|next\s+)?"
                       r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d)",
                       re.I)
_CONFIRMED = re.compile(r"\b(confirmed|officially|it'?s official)\b", re.I)
_TRENDING = re.compile(r"\b(trending|viral|blowing up|everyone(?:'s| is) talking)\b", re.I)
_QUOTED = re.compile(r"[“\"]([^”\"]{2,80})[”\"]")


def is_music(facts: dict) -> bool:
    return facts.get("format") in config.MUSIC_KINDS


def format_key(facts: dict) -> str:
    """The video format: a story's kind decides it; the roundups are their own format."""
    if facts.get("format") == "music_story":
        return STORY_FORMAT.get(facts.get("story_type"), "music_news")
    return facts.get("format") or "music_news"


def status_phrase(facts: dict) -> str | None:
    """How the release status has to be said. None when the story isn't about a release."""
    status, when = facts.get("release_status"), facts.get("release_date")
    if facts.get("release_dates_reported"):
        return "announced, but the sources disagree on the date"
    return {
        "rumored": "rumored, not confirmed",
        "teased": "teased, not announced yet",
        "announced": f"announced for {when}" if when else "announced, no date yet",
        "scheduled": f"out {when}" if when else "announced, no date yet",
        "released": "out now",
    }.get(status)


def _subject(facts: dict) -> str:
    title = facts.get("release_title")
    return f"“{title}”" if title else "it"


# ---------- angles (hooks built from facts only) ----------

def _angle(key, label, hook, pitch, style, cta) -> dict:
    return {"key": key, "label": label, "hook": hook, "pitch": pitch, "style": style, "cta": cta}


def _news_hook(facts: dict) -> str:
    fmt, artist, status = format_key(facts), facts.get("artist", ""), facts.get("release_status")
    title = facts.get("release_title")
    others = facts.get("other_artists") or []
    if fmt == "album_announcement":
        if status in ("rumored",):
            return f"Is {artist} about to drop an album?"
        if facts.get("release_date") and status in ("announced", "scheduled"):
            return f"New {artist} album, {facts['release_date'].split(', ')[-1]}"
        return f"{artist} just announced a new album" if status in ("announced", "scheduled") else f"{artist} album news"
    if fmt == "tease":
        return f"{artist} is teasing something"
    if fmt == "new_release":
        return f"New {artist}: {title}" if title and status == "released" else f"New music from {artist}"
    if fmt == "collaboration":
        return f"{artist} and {others[0]}? Here's what we know" if others else f"{artist} has a new collab"
    if fmt == "tour":
        return f"{artist} tour news, and the Philly date" if facts.get("philly_shows") else f"{artist} tour news"
    return f"What's going on with {artist}"


def angles_for(facts: dict) -> list[dict]:
    fmt = facts.get("format")
    if fmt == "music_story":
        artist = facts.get("artist", "")
        careful = facts.get("sensitive") or facts.get("story_type") in ("legal", "sensitive")
        if careful:  # no hype hooks on deaths, health, arrests, lawsuits
            return [_angle("news", "What we know", f"What we know about {artist} so far",
                           "Calm and factual: what the sources say, nothing more. No jokes.", "voiceover",
                           "Follow for music news.")]
        out = [
            _angle("news", "The news", _news_hook(facts), "Straight news: what happened, the status, who reports it.",
                   "face-to-camera", "Follow for music news."),
            _angle("fan", "For the fans", f"{artist} fans, this one's for you",
                   "Talk to the fanbase: the news, then your take.", "face-to-camera", "Tell me what you think."),
        ]
        shows = facts.get("philly_shows") or []
        if shows:
            s = shows[0]
            out.append(_angle("philly", "Philly link", f"{artist} news, and they play Philly {s['date'].split(', ')[-1]}",
                              f"The news, then the Philly date: {s['date']} at {s['venue']}.", "face-to-camera",
                              "Tag who you're going with."))
        return out
    n = len(_items(facts))
    if fmt == "new_music_friday":
        return [
            _angle("news", "The list", f"New Music Friday: {n} releases worth a listen",
                   "One slide per release; a line from you on each.", "slideshow", _CLOSER[fmt]),
            _angle("fan", "Deserves a listen", "Here's what actually deserves a listen this Friday",
                   "Your picks: lead with your favorite, say why.", "face-to-camera", _CLOSER[fmt]),
        ]
    if fmt == "upcoming_albums":
        month = facts["month"].split()[0]
        return [
            _angle("news", "The list", f"{n} albums I'm watching in {month}",
                   "One slide per album with its date and status.", "slideshow", "Save this for later."),
            _angle("fan", "Most anticipated", f"The {month} album I'm waiting on most",
                   "Count down to your most anticipated.", "face-to-camera", _CLOSER[fmt]),
        ]
    if fmt == "emerging_artists":
        return [
            _angle("news", "Before they blow up", f"{n} artists to listen to before everyone else does",
                   "One slide per artist with why they're on your radar (the evidence).", "slideshow", _CLOSER[fmt]),
            _angle("fan", "If you like…", f"If you like {facts['because'][0]}, you need to hear these"
                   if facts.get("because") else "If you like my usual stuff, you need to hear these",
                   "Start from an artist your audience knows, then the new names.", "face-to-camera", _CLOSER[fmt]),
        ]
    return []


def fallback_hook(facts: dict) -> str:
    first = angles_for(facts)
    return first[0]["hook"] if first else f"Music news: {facts.get('artist', '')}".strip()


def hook_names(facts: dict) -> list[str]:
    if facts.get("format") == "music_story":
        return [facts["artist"]] if facts.get("artist") else []
    return [config.SERIES_FOR_FORMAT.get(facts.get("format"), "")] + [i["artist"] for i in _items(facts)]


# ---------- the prompt ----------

def _strip_urls(value):
    if isinstance(value, dict):
        return {k: _strip_urls(v) for k, v in value.items() if k != "url"}
    if isinstance(value, list):
        return [_strip_urls(v) for v in value]
    return value


def prompt_facts(facts: dict) -> dict:
    """What the model sees: no links, no ids, nothing personal (your plays stay on the dashboard)."""
    out = {k: v for k, v in facts.items() if k not in ("format", "story_id", "your_plays", "sensitive")}
    phrase = status_phrase(facts)
    if phrase:
        out["status_phrase"] = phrase
    return _strip_urls(out)


def guide(facts: dict) -> str:
    fmt = format_key(facts)
    lines = [f"Video: music news about {facts.get('artist')}. Format: {FORMAT_LABEL[fmt]}. {_GUIDE.get(fmt, _GUIDE['music_news'])}"]
    phrase = status_phrase(facts)
    if phrase:
        lines.append(f"Release status: {phrase}. The script must say this (in these words or plainer, never stronger).")
    if facts.get("release_dates_reported"):
        lines.append("The sources give different dates: say the date isn't settled yet. Don't pick one.")
    if facts.get("verification") == "unverified":
        outlet = (facts.get("sources") or [{}])[0].get("outlet", "the outlet")
        lines.append(f"Only one outlet reports this so far: attribute it (\"according to {outlet}\").")
    if facts.get("sensitive"):
        lines.append("Sensitive story (death, health, legal): calm and respectful, no hype, no jokes.")
    if facts.get("philly_shows") and fmt != "tour":
        lines.append("They also have a Philly date (philly_shows): you may mention it, copied exactly.")
    if facts.get("previous_releases"):
        lines.append("previous_releases are from the release catalogs: you may name one for context.")
    lines.append("Write only the news. The creator's own take is added between your script and your closer.")
    lines.append('Example hook: "[Artist] just announced a new album"')
    return "\n".join(lines)


# ---------- templated scripts for the roundups ----------

def _items(facts: dict) -> list[dict]:
    return facts.get("releases") or facts.get("albums") or facts.get("artists_to_watch") or []


def take_line(facts: dict) -> str:
    """The beat you fill in. A prompt built from the facts, never an opinion."""
    fmt = format_key(facts)
    hint = _TAKE_HINT.get(fmt, _TAKE_HINT["music_news"])
    prev = (facts.get("previous_releases") or [None])[0]
    if prev and fmt in ("album_announcement", "new_release", "tease"):
        hint = f"{hint}; how it compares to “{prev['title']}” ({prev['date']})"
    return f"{TAKE} {hint}" + ("" if hint.endswith(("?", ".", ")")) else ".")


def assemble(script: str, closer: str, facts: dict) -> str:
    """Model script + your-take line + model closer."""
    return "\n\n".join(p for p in (script.strip(), take_line(facts), closer.strip()) if p)


def script_for(facts: dict) -> str | None:
    """The voiceover for the roundups (no model needed). None for music_story: the model writes it."""
    fmt, items = facts.get("format"), _items(facts)
    if fmt == "new_music_friday":
        lines = [f"New Music Friday, {templates.speakable(facts['week'])}. From the artists I keep on repeat:"]
        lines += [f"{i['artist']}, “{i['title']}”, a new {_TYPE_WORD.get(i['type'], 'release')}." for i in items]
    elif fmt == "upcoming_albums":
        lines = [f"The albums I'm watching in {facts['month'].split()[0]}."]
        lines += [f"{i['artist']}, “{i['title']}”, {templates.speakable(_album_status(i))}." for i in items]
    elif fmt == "emerging_artists":
        lines = ["A few artists to have on your radar."]
        lines += [f"{i['artist']}. {i['why']}." for i in items]
    else:
        return None
    return "\n".join(lines) + "\n\n" + take_line(facts) + "\n\n" + _CLOSER[fmt]


def _album_status(i: dict) -> str:
    if i.get("date"):
        return f"out {i['date']}" if i.get("status") in ("scheduled", "released", None) else f"announced for {i['date']}"
    return "announced, no date yet"


def caption_for(facts: dict) -> str | None:
    fmt, items = facts.get("format"), _items(facts)
    names = "; ".join(f"{i['artist']} – {i['title']}" if i.get("title") else i["artist"] for i in items[:5])
    more = " and more" if len(items) > 5 else ""
    if fmt == "new_music_friday":
        return f"New Music Friday 🎧 {names}{more}. {_CLOSER[fmt]}"
    if fmt == "upcoming_albums":
        return f"{facts['month'].split()[0]} albums I'm watching 📅 {names}{more}. Save this for later."
    if fmt == "emerging_artists":
        return f"Artists to watch 👀 {names}{more}. {_CLOSER[fmt]}"
    return None


def _tag(name: str) -> str:
    return "#" + "".join(ch for ch in name if ch.isalnum())


def hashtags(facts: dict) -> list[str]:
    base = {"new_music_friday": ["#newmusicfriday", "#newmusic"], "upcoming_albums": ["#newalbums", "#upcomingalbums"],
            "emerging_artists": ["#newartists", "#musicdiscovery"]}.get(facts.get("format"), ["#musicnews"])
    names = [facts.get("artist")] + [i["artist"] for i in _items(facts)]
    return list(dict.fromkeys(base + [_tag(n) for n in names if n]))[:8]


# ---------- on screen ----------

def onscreen(facts: dict) -> list[str]:
    fmt = facts.get("format")
    if fmt == "music_story":
        lines = [f"{KICKER[format_key(facts)]}: {facts.get('artist', '')}"]
        phrase = status_phrase(facts)
        if facts.get("release_title") or phrase:
            lines.append(" · ".join(b for b in (f"“{facts['release_title']}”" if facts.get("release_title") else None,
                                                  phrase.upper() if phrase else None) if b))
        for r in facts.get("release_dates_reported") or []:
            lines.append(f"{r['date']} (per {r['from']})")
        for s in (facts.get("philly_shows") or [])[:2]:
            lines.append(f"PHILLY: {s['date']} · {s['venue']}")
        outlets = list(dict.fromkeys(s["outlet"] for s in facts.get("sources") or []))
        if outlets:
            lines.append("via " + ", ".join(outlets[:2]))
        return lines
    items = _items(facts)
    if fmt == "new_music_friday":
        return [f"NEW MUSIC FRIDAY · {facts['week']}"] + [
            f"{i['artist']} · {i['title']} · {_TYPE_WORD.get(i['type'], 'release').upper()}" for i in items]
    if fmt == "upcoming_albums":
        return [f"ALBUMS I'M WATCHING · {facts['month'].upper()}"] + [
            " · ".join([i.get("date") or "DATE TBA", i["artist"], i["title"]]) for i in items]
    if fmt == "emerging_artists":
        return ["ARTISTS TO WATCH"] + [f"{i['artist']} · {i['why']}" for i in items]
    return []


# ---------- fact checks (on top of llm.check_facts' dates and numbers) ----------

def check(draft: dict, facts: dict) -> list[str]:
    text = " ".join([draft.get("hook", ""), draft.get("script", ""), draft.get("caption", "")])
    spoken_text = " ".join([draft.get("hook", ""), draft.get("script", "")]).lower()
    status = facts.get("release_status")
    roundup = facts.get("format") != "music_story"
    warnings = []
    if _OUT_NOW.search(text) and status != "released" and facts.get("format") != "new_music_friday":
        warnings.append(f"Says “{_OUT_NOW.search(text).group(0)}”, but the release is {status_phrase(facts) or 'not out per the facts'}")
    m = _DROPS_ON.search(text)
    if m and not roundup and (not facts.get("release_date") or facts.get("release_dates_reported")):
        warnings.append(f"Says “{m.group(0)}”, but the facts have no settled release date")
    m = _CONFIRMED.search(text)
    if m and (status in ("rumored", "teased") or facts.get("verification") == "conflicting"):
        warnings.append(f"Says “{m.group(0)}”, but it's {status_phrase(facts) or facts.get('verification')}")
    m = _TRENDING.search(text)
    if m and not (facts.get("momentum") or {}).get("two_signals"):
        warnings.append(f"Says “{m.group(0)}”: nothing in the data shows that")
    fact_text = str(facts).casefold()
    for q in _QUOTED.findall(text):
        if q.strip(" .,!?").casefold() not in fact_text:
            warnings.append(f"Not in facts: “{q}”")
    phrase = status_phrase(facts)
    if phrase and not roundup and not any(w in spoken_text for w in STATUS_WORDS.get(status, ())):
        warnings.append(f"Doesn't say the release status ({phrase})")
    return warnings


# ---------- the catalog roundups (the news job builds them) ----------

_TIER_RANK = {"core": 0, "watch": 1, "adjacent": 2, "breakout": 3}
_TYPE_RANK = {"album": 0, "ep": 1, "single": 2}


def _catalog_rows(conn, start: str, end: str, types: tuple[str, ...]) -> list[dict]:
    """Watchlist releases dated in [start, end], one per artist + title, providers merged. Guest spots left out."""
    taste = listening.load()
    found: dict[tuple, dict] = {}
    for r in conn.execute(
            f"""SELECT r.*, w.name AS artist_name, w.tier, w.key AS artist_key FROM releases r
                JOIN artist_identities w ON w.id = r.artist_id
                WHERE r.release_date BETWEEN ? AND ? AND w.muted = 0 AND w.tier IN ('core', 'watch', 'adjacent', 'breakout')
                  AND r.record_type IN ({','.join('?' * len(types))})
                ORDER BY r.release_date, r.provider""", (start, end, *types)):
        if featured(r["title"], r["artist_name"]):
            continue
        k = (r["artist_id"], r["title_key"])
        item = found.setdefault(k, {"artist": r["artist_name"], "title": clean_title(r["title"]),
                                    "type": r["record_type"], "date": fmt_date(r["release_date"]), "from": [],
                                    "url": r["url"], "_iso": r["release_date"], "_tier": r["tier"],
                                    "_plays": taste.plays.get(r["artist_key"], 0), "_artist_id": r["artist_id"]})
        if _TYPE_RANK.get(r["record_type"], 3) < _TYPE_RANK.get(item["type"], 3):
            item["type"] = r["record_type"]
        if r["provider"] not in item["from"]:
            item["from"].append(r["provider"])
    return list(found.values())


def _rank(items: list[dict]) -> list[dict]:
    """Your core artists first, then albums before singles, then plays (ranking only; plays never reach the facts).
    At most MUSIC_ROUNDUP_PER_ARTIST each, so one busy artist doesn't fill the list."""
    items.sort(key=lambda i: (_TIER_RANK.get(i["_tier"], 4), _TYPE_RANK.get(i["type"], 3), -i["_plays"], i["artist"]))
    out, per = [], {}
    for i in items:
        per[i["_artist_id"]] = per.get(i["_artist_id"], 0) + 1
        if per[i["_artist_id"]] <= config.MUSIC_ROUNDUP_PER_ARTIST:
            out.append(i)
    return out[:config.MUSIC_ROUNDUP_MAX]


def _public(items: list[dict]) -> list[dict]:
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]


def _at(day: date, hour: int) -> str:
    return datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour).astimezone(timezone.utc).isoformat()


def new_music_friday(conn, now: datetime) -> dict | None:
    """Thursday to Sunday: the week's releases (Saturday through Friday) from the catalogs."""
    today = now.astimezone(TZ).date()
    if today.weekday() not in (3, 4, 5, 6):
        return None
    friday = today + timedelta(days=4 - today.weekday())
    items = _rank(_catalog_rows(conn, (friday - timedelta(days=6)).isoformat(), friday.isoformat(), ("album", "ep", "single")))
    if len(items) < config.MUSIC_ROUNDUP_MIN:
        return None
    facts = {"format": "new_music_friday", "week": fmt_date(friday.isoformat()), "releases": _public(items),
             "release_count": len(items)}
    return {"key": f"nmf:{friday.isoformat()}", "kind": "new_music_friday",
            "title": f"New Music Friday · {facts['week']} ({len(items)} releases)",
            "score": min(85, 55 + 4 * len(items)), "facts": facts,
            "post_by": _at(friday, 18), "expires_at": _at(friday + timedelta(days=3), 0)}


def upcoming_albums(conn, now: datetime) -> dict | None:
    """From the 15th: next month's albums and EPs, from catalog pre-orders and dated announcements."""
    today = now.astimezone(TZ).date()
    if today.day < 15:
        return None
    first = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    items = {(i["_artist_id"], title_key(i["title"])): {**i, "status": "scheduled"}
             for i in _catalog_rows(conn, first.isoformat(), last.isoformat(), ("album", "ep"))}
    for s in conn.execute(
            """SELECT s.*, w.name AS artist_name, w.tier, w.key AS artist_key FROM stories s
               JOIN artist_identities w ON w.id = s.primary_artist_id
               WHERE s.release_date BETWEEN ? AND ? AND s.story_type IN ('album_announcement', 'tracklist', 'album_release')
                 AND s.verification != 'conflicting' AND s.release_title IS NOT NULL AND w.muted = 0
                 AND s.release_status IN ('announced', 'scheduled')""", (first.isoformat(), last.isoformat())):
        k = (s["primary_artist_id"], title_key(s["release_title"]))
        outlets = [r["name"] for r in conn.execute(
            """SELECT DISTINCT n.name FROM story_articles x JOIN articles a ON a.id = x.article_id
               JOIN news_sources n ON n.id = a.source_id WHERE x.story_id = ?""", (s["id"],))]
        if k in items:
            items[k]["from"] += [o for o in outlets if o not in items[k]["from"]]
            continue
        items[k] = {"artist": s["artist_name"], "title": s["release_title"], "type": "album",
                    "date": fmt_date(s["release_date"]), "status": s["release_status"], "from": outlets,
                    "url": None, "_iso": s["release_date"], "_tier": s["tier"], "_plays": 0,
                    "_artist_id": s["primary_artist_id"]}
    ranked = _rank(list(items.values()))
    if len(ranked) < config.MUSIC_ROUNDUP_MIN:
        return None
    ranked.sort(key=lambda i: i["_iso"])  # a calendar reads in date order
    month = f"{first:%B %Y}"
    facts = {"format": "upcoming_albums", "month": month, "albums": _public(ranked), "album_count": len(ranked)}
    return {"key": f"upcoming:{first:%Y-%m}", "kind": "upcoming_albums",
            "title": f"Albums to watch in {first:%B} ({len(ranked)})", "score": min(80, 50 + 4 * len(ranked)),
            "facts": facts, "post_by": _at(first, 0), "expires_at": _at(first + timedelta(days=7), 0)}


def roundups(conn, now: datetime | None = None) -> list[int]:
    """Raise or refresh this week's New Music Friday and next month's albums. Returns new alert ids."""
    now = now or datetime.now(timezone.utc)
    alerts = [a for a in (new_music_friday(conn, now), upcoming_albums(conn, now)) if a]
    return save_alerts(conn, alerts) if alerts else []


def roundup_evaluation(alert: dict) -> dict:
    """The queue's view of a roundup: its own score (set when built), with what it lists as the reason."""
    facts = alert["facts"]
    items = _items(facts)
    reasons = {"new_music_friday": f"{len(items)} releases this week from your watchlist, all from the release catalogs",
               "upcoming_albums": f"{len(items)} albums due in {facts.get('month', 'next month')}",
               "emerging_artists": f"{len(items)} artists from Discover"}
    caveats = []
    if facts.get("format") == "upcoming_albums":
        caveats.append("Release dates can move: check them again before posting")
    if facts.get("format") == "new_music_friday":
        caveats.append("Some may come out later in the week: the catalogs list pre-orders ahead")
    return {"reasons": [reasons.get(facts.get("format"), "")], "caveats": caveats}
