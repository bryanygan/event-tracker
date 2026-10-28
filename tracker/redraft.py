"""A new draft with your notes ("New draft with notes" on a card): what to include, leave out or emphasize.

- Your note steers the model. It's an instruction, never a fact: the prompt says so, and check_facts still flags
  anything in the draft that isn't in the facts.
- A show you add comes from the events table (event_facts), so its date, venue and price are data. A show you leave
  out is dropped from the facts the draft (and the alert's slides) are built from.
- Both are saved on the draft (`drafts.instructions`). A later facts refresh is compared with the same edits
  applied, so a show you added isn't reported as "Removed" just because the idea itself still leaves it out.
- `why_left_out` explains from the data why a show isn't in an idea (its genre, its score, its status), for the
  "facts changed" lines and the shows you can add.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, search
from .detectors import event_facts, fmt_date
from .scoring import is_game, is_music, upcoming_events

MAX_NOTE = 1000
MAX_ADD = 10
# Facts formats whose shows you can add to or leave out (single-show ideas have nothing to choose)
LIST_FORMATS = {"weekend", "stacked_month", "things_to_do", "team_week"}
GENRE_SOURCE = {"listing": "the ticket listing", "artist": "Ticketmaster's genre for the artist",
                "tm_search": "Ticketmaster's genre for the artist", "deezer": "Deezer's genre for the artist"}


class RedraftError(ValueError):
    pass


def today_et() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def show_key(s: dict) -> str:
    """The dashboard's key for a show in a list (the same one it stars picks with)."""
    return f"{s.get('artist') or s.get('event_name')}|{s.get('date')}"


def label(s: dict) -> str:
    return f"{s.get('artist') or s.get('event_name')} ({s.get('date')})"


def editable(facts: dict) -> bool:
    return facts.get("format") in LIST_FORMATS and isinstance(facts.get("shows"), list)


def validate(note: str | None, add: list[str] | None, drop: list[str] | None, facts: dict) -> dict | None:
    """Your request as stored on the draft, or None when it asks for nothing."""
    note = (note or "").strip()
    add, drop = list(dict.fromkeys(add or [])), list(dict.fromkeys(drop or []))
    if len(note) > MAX_NOTE:
        raise RedraftError(f"Keep the note under {MAX_NOTE} characters")
    if (add or drop) and not editable(facts):
        raise RedraftError("This idea has no list of shows to add to or leave out")
    if len(add) > MAX_ADD:
        raise RedraftError(f"Add at most {MAX_ADD} shows at a time")
    have = {show_key(s) for s in facts.get("shows") or []}
    unknown = [k for k in drop if k not in have]
    if unknown:
        raise RedraftError(f"Not in this idea's shows: {', '.join(unknown)}")
    if drop and len(drop) >= len(have) and not add:
        raise RedraftError("Leave at least one show in")
    if not (note or add or drop):
        return None
    return {"note": note, "add": add, "drop": drop}


def _sort_date(label_date: str | None, today: date) -> str:
    """'Tue, Nov 17' -> '2026-11-17' (the year that keeps it upcoming), for ordering added shows among the rest."""
    try:
        d = datetime.strptime(f"{label_date.split(', ', 1)[1]} {today.year}", "%b %d %Y").date()
    except (AttributeError, IndexError, ValueError):
        return "9999"
    return (d.replace(year=today.year + 1) if d < today.replace(day=1) else d).isoformat()


def apply(conn, facts: dict, ins: dict | None, today: date | None = None) -> tuple[dict, list[str]]:
    """The facts with your shows added and left out, and problems to show on the draft (a show you added that's
    no longer listed). Counts that the video states (notable_show_count) follow the edit."""
    if not ins or not editable(facts) or not (ins.get("add") or ins.get("drop")):
        return facts, []
    today = today or today_et()
    drop = set(ins.get("drop") or [])
    shows = [s for s in facts["shows"] if show_key(s) not in drop]
    problems, have = [], {show_key(s) for s in shows}
    if ins.get("add"):
        events = {e["id"]: e for e in upcoming_events(conn, today.isoformat())}
        for event_id in ins["add"]:
            e = events.get(event_id)
            if e is None or e["status"] in ("cancelled", "postponed"):
                problems.append(f"A show you added is no longer listed or was {e['status'] if e else 'removed'}")
                continue
            f = event_facts(e)
            if show_key(f) not in have:
                have.add(show_key(f))
                shows.append(f)
    shows.sort(key=lambda s: _sort_date(s.get("date"), today))
    out = {**facts, "shows": shows}
    if facts.get("picks"):
        out["picks"] = [p for p in facts["picks"] if show_key(p) not in drop]
        out["pick_count"] = len(out["picks"])
    if facts.get("notable_show_count") is not None:
        before = {s.get("artist") for s in facts["shows"]}
        after = {s.get("artist") for s in shows}
        out["notable_show_count"] = max(len(after), facts["notable_show_count"] + len(after - before) - len(before - after))
    return out, problems


def note_warnings(conn, facts: dict, note: str | None, today: date | None = None) -> list[str]:
    """Your note names an upcoming act who isn't in the draft's shows: the model can't add them (no facts), so say
    how to. Only for list ideas, and only names of 4+ letters (short ones match ordinary words)."""
    if not note or not editable(facts):
        return []
    today = today or today_et()
    in_draft = {(s.get("artist") or "").casefold() for s in facts["shows"]}
    text, named = note.casefold(), []
    for e in upcoming_events(conn, today.isoformat()):
        name = (e["headliner"] or "").strip()
        if len(name) >= 4 and name.casefold() not in in_draft and name not in named and \
                re.search(r"(?<![\w])" + re.escape(name.casefold()) + r"(?![\w])", text):
            named.append(name)
    return [f"Your note names {n}, who isn't in this draft's shows. The draft can't mention them without their "
            f"facts: use “Add a show” to include them." for n in named[:3]]


# ---------- why a show isn't in an idea ----------

def why_left_out(facts: dict, e: dict | None, today: date | None = None) -> str:
    """One line, from the data, on why event `e` (an upcoming_events row, or None if it's gone) isn't in the idea."""
    today = today or today_et()
    if e is None:
        return "no longer listed by Ticketmaster or the venue"
    if e["local_date"] < today.isoformat():
        return "already happened"
    if e["status"] in ("cancelled", "postponed"):
        return e["status"]
    fmt = facts.get("format")
    if fmt == "stacked_month":
        if e["genre"] != facts.get("genre"):
            src = GENRE_SOURCE.get(e.get("genre_source"), "the listing")
            also = (f"; Deezer says {e['headliner_deezer_genre']}" if e.get("headliner_deezer_genre")
                    and e["headliner_deezer_genre"] not in (e["genre"], None) else "")
            return f"listed as {e['genre'] or 'no genre'} ({src}{also}), not {facts.get('genre')}"
        if e["score"] < config.STACKED_MONTH_MIN_SCORE:
            return (f"its score ({e['score']:.0f}) is under the {config.STACKED_MONTH_MIN_SCORE} a show needs to "
                    f"count as notable")
        return "not in the month's top 10 by score"
    if fmt == "weekend" and facts.get("lane"):
        return f"not one of the top {config.WEEKEND_TOP_N} {facts['lane']} shows by score"
    if fmt == "weekend":
        if not (is_music(e) or is_game(e)):
            return "comedy and markets go in “Things to do” instead"
        return (f"the roundup keeps the top {config.WEEKEND_TOP_N} shows by score, at most 2 per genre and "
                f"{config.WEEKEND_MAX_GAMES} games (this one scores {e['score']:.0f})")
    return f"not one of the shows this idea picked (it keeps the highest-scoring ones; this one scores {e['score']:.0f})"


def _find(conn, show: dict, upcoming: dict[str, dict]) -> tuple[bool, dict | None]:
    """(found in the events table at all, its upcoming row or None). Matched by ticket link or by name + venue,
    then by name and date (one link can cover several listings)."""
    rows = conn.execute("""SELECT e.id FROM events e JOIN venues v ON v.id = e.venue_id
                           WHERE e.url = ? OR ((e.name = ? OR e.name = ?) AND v.name = ?)""",
                        (show.get("url") or "", show.get("event_name"), show.get("artist"), show.get("venue"))).fetchall()
    names = {show.get("artist"), show.get("event_name")} - {None}
    for r in rows:
        e = upcoming.get(r["id"])
        if e and (e["headliner"] in names or e["name"] in names) and fmt_date(e["local_date"]) == show.get("date"):
            return True, e
    return bool(rows), None


def removal_reasons(conn, old: dict, new: dict, today: date | None = None) -> dict[str, str]:
    """For each show in `old` that isn't in `new`: label -> why (from the events table as it is now)."""
    gone = [s for s in old.get("shows") or [] if show_key(s) not in {show_key(n) for n in new.get("shows") or []}]
    if not gone:
        return {}
    today = today or today_et()
    upcoming = {e["id"]: e for e in upcoming_events(conn, today.isoformat())}
    out = {}
    for s in gone:
        found, e = _find(conn, s, upcoming)
        if e is None and found and _sort_date(s.get("date"), today) < today.isoformat():
            out[label(s)] = "already happened"
        else:
            out[label(s)] = why_left_out(new, e, today)
    return out


def explain(lines: list[str], reasons: dict[str, str]) -> list[str]:
    """'Removed: X (Tue, Nov 17)' -> 'Removed: X (Tue, Nov 17): listed as Pop (...), not Dance/Electronic'."""
    out = []
    for line in lines:
        what = line[len("Removed: "):] if line.startswith("Removed: ") else None
        out.append(f"{line}: {reasons[what]}" if what in reasons else line)
    return out


# ---------- shows you could add ----------

def _window(conn, alert: dict, facts: dict) -> tuple[str, str] | None:
    """The dates an idea covers: its month for a stacked month, else the span of its own events."""
    if facts.get("format") == "stacked_month" and facts.get("month"):
        start = datetime.strptime(facts["month"], "%B %Y").date()
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start.isoformat(), (next_month - timedelta(days=1)).isoformat()
    ids = json.loads(alert["event_ids"] or "[]")
    if not ids:
        return None
    rows = conn.execute(f"SELECT MIN(local_date), MAX(local_date) FROM events WHERE id IN ({','.join('?' * len(ids))})",
                        ids).fetchone()
    return (rows[0], rows[1]) if rows[0] else None


def candidates(conn, alert: dict, q: str | None = None, today: date | None = None, limit: int = 15) -> list[dict]:
    """Shows you could add to a list idea: in its dates (or matching `q` anywhere upcoming), not already in it,
    best match first. A stacked month suggests shows whose genre matches by any source (the listing, the artist
    on Ticketmaster, or Deezer), so a show filed under a neighbouring genre still turns up. Each says why it's out."""
    facts = json.loads(alert["facts"])
    if not editable(facts):
        return []
    today = today or today_et()
    window = _window(conn, alert, facts)
    have = {show_key(s) for s in facts["shows"]}
    want_genre = facts.get("genre") if facts.get("format") == "stacked_month" else None
    music_only = facts.get("format") in ("stacked_month",) or (facts.get("format") == "weekend" and facts.get("lane"))
    out = []
    for e in upcoming_events(conn, today.isoformat()):
        if e["status"] in ("cancelled", "postponed") or show_key(event_facts(e)) in have:
            continue
        if q:
            if not search.matches(e, q):
                continue
        elif not window or not (window[0] <= e["local_date"] <= window[1]):
            continue
        if music_only and not is_music(e):
            continue
        genres = {e.get("genre"), e.get("ticketmaster_genre"), e.get("headliner_genre"), e.get("headliner_deezer_genre")}
        match = bool(want_genre) and want_genre in genres
        if not q and want_genre and not match:
            continue
        in_window = bool(window) and window[0] <= e["local_date"] <= window[1]
        f = event_facts(e)
        out.append({"id": e["id"], "artist": f.get("artist"), "date": f.get("date"), "venue": f.get("venue"),
                    "genre": e["genre"], "score": round(e["score"]),
                    "why_not": why_left_out(facts, e, today) if in_window else "outside this idea's dates",
                    "_rank": (not match, not in_window, -e["score"])})
    out.sort(key=lambda c: c["_rank"])
    seen, unique = set(), []
    for c in out:  # a two-night run once (its first night)... unless you searched for it
        k = (c["artist"], c["venue"])
        if q or k not in seen:
            seen.add(k)
            unique.append({k2: v for k2, v in c.items() if k2 != "_rank"})
    return unique[:limit]


def edited_facts(conn, alert_id: int, facts: dict) -> dict:
    """The alert's facts with its newest draft's show edits applied, so its slides list the same shows as the
    script you'll read over them."""
    row = conn.execute("SELECT instructions FROM drafts WHERE alert_id=? ORDER BY id DESC LIMIT 1",
                       (alert_id,)).fetchone()
    ins = json.loads(row["instructions"]) if row and row["instructions"] else None
    return apply(conn, facts, ins)[0]
