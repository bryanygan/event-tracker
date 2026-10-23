"""Genres for shows whose listing doesn't have one (roadmap P1-10).

Venue calendars give no genre at all, and ~1 in 5 Ticketmaster events come back "Undefined". Without a
genre a show can't count toward a genre roundup, so those shows were invisible to "stacked month" and the
lane weekend cut. Two fills, both from Ticketmaster's own classification of the artist:

  "artist"    the event's headliner is a Ticketmaster attraction with a genre (the event itself had none)
  "tm_search" a venue-calendar act looked up by exact name on Ticketmaster's attraction search

Rules that keep this defensible:
- Exact (case-insensitive) name match only, and only when exactly one music attraction has that name:
  two acts sharing a name is "ambiguous" and stays unknown (no namesake merging).
- Names that look like event titles (open mic, karaoke, "... night") are never looked up.
- Placeholder labels (Undefined, Other, ...) are never a genre.
- Results, including "none" and "ambiguous", are cached on the attraction for GENRE_RECHECK_DAYS.
- Capped per run, and a failed lookup never fails the run.

A third source, Deezer (the most common genre of the headliner's releases, fetchers/deezer.py), fills shows
nothing else covers and overrides Ticketmaster when the two disagree across genre families: Ticketmaster
filed a hyperpop act under Rock and a rock band under Dance/Electronic. Within a family (Pop vs Hip-Hop/Rap)
Ticketmaster's label stays.

The stored listing genre is never overwritten: the fill happens when events are read (scoring.upcoming_events),
and every event says where its genre came from in `genre_source` ("listing", "artist", "tm_search", "deezer"
or None).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from . import config
from .store import now_iso

log = logging.getLogger(__name__)
TZ = ZoneInfo(config.TIMEZONE)

_SKIP = [re.compile(p, re.I) for p in config.GENRE_LOOKUP_SKIP_PATTERNS]

SOURCE_LABEL = {"listing": "from the listing", "artist": "Ticketmaster's genre for the artist",
                "tm_search": "matched by name on Ticketmaster (inferred)",
                "deezer": "the most common genre of the artist's Deezer releases"}


def usable(genre: str | None) -> bool:
    return bool(genre) and genre not in config.NON_GENRES


def fill(row: dict) -> None:
    """Set row['genre'] / ['subgenre'] / ['genre_source'] from the listing, else the headliner attraction,
    else Deezer; Deezer also replaces a listing or artist genre from a different family (config.GENRE_FAMILIES).
    `row` is an EVENT_VIEW_SQL row (has headliner_genre, headliner_subgenre, headliner_genre_source,
    headliner_deezer_genre). A replaced label is kept in row['ticketmaster_genre']."""
    if usable(row.get("genre")):
        row["genre_source"] = "listing"
        if not usable(row.get("subgenre")):
            row["subgenre"] = None
    else:
        row["genre"], row["genre_source"] = None, None
        if usable(row.get("headliner_genre")):
            row["genre"] = row["headliner_genre"]
            row["subgenre"] = row["headliner_subgenre"] if usable(row.get("headliner_subgenre")) else None
            row["genre_source"] = row.get("headliner_genre_source") or "artist"
        elif not usable(row.get("subgenre")):
            row["subgenre"] = None
    deezer = row.get("headliner_deezer_genre")
    if not deezer or row.get("segment") not in (None, "Music"):  # a play's headliner may share a musician's name
        return
    if row["genre"] is None:
        row["genre"], row["subgenre"], row["genre_source"] = deezer, None, "deezer"
        return
    family, other = config.GENRE_FAMILIES.get(row["genre"]), config.GENRE_FAMILIES.get(deezer)
    if family and other and family != other and deezer not in config.DEEZER_FILL_ONLY:
        row["ticketmaster_genre"] = row["genre"]
        row["genre"], row["subgenre"], row["genre_source"] = deezer, None, "deezer"


def lookup_candidates(conn, limit: int, now: datetime | None = None) -> list[dict]:
    """Venue-calendar headliners with upcoming shows, no genre, and not checked recently."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=config.GENRE_RECHECK_DAYS)).isoformat()
    rows = conn.execute(  # soonest shows first: they're the ones a roundup needs this week
        """SELECT a.id, a.name FROM attractions a
           JOIN event_attractions ea ON ea.attraction_id = a.id AND ea.position = 0
           JOIN events e ON e.id = ea.event_id AND e.active = 1 AND e.local_date >= ?
           WHERE a.id LIKE 'name:%' AND a.genre IS NULL
             AND (a.genre_checked_at IS NULL OR a.genre_checked_at < ?)
           GROUP BY a.id ORDER BY MIN(e.local_date), a.name""",
        (now.astimezone(TZ).date().isoformat(), cutoff)).fetchall()
    out = []
    for r in rows:
        name = (r["name"] or "").strip()
        # 1-2 letter names ("EQ") match some unrelated act far too easily
        if len(re.sub(r"\W", "", name)) < 3 or len(name.split()) > 6 or any(p.search(name) for p in _SKIP):
            continue
        out.append({"id": r["id"], "name": name})
        if len(out) >= limit:
            break
    return out


def fill_from_ticketmaster(conn, client, limit: int = config.GENRE_LOOKUPS_PER_RUN) -> dict:
    """Look up venue-calendar acts on Ticketmaster. Returns counts by outcome. Never raises for API errors."""
    from .fetchers.ticketmaster import BudgetExceeded, attraction_genre
    counts = {"found": 0, "none": 0, "ambiguous": 0, "no_genre": 0}
    for cand in lookup_candidates(conn, limit):
        try:
            status, attraction = client.find_attraction(cand["name"])
        except (requests.RequestException, BudgetExceeded, RuntimeError, ValueError) as exc:
            log.warning("Ticketmaster genre lookup stopped at %r: %s", cand["name"], exc)
            break
        genre, subgenre = attraction_genre(attraction) if attraction else (None, None)
        if status == "found" and not genre:
            status = "no_genre"
        counts[status] += 1
        conn.execute("""UPDATE attractions SET genre=?, subgenre=?, genre_source=?, genre_checked_at=?
                        WHERE id=?""",
                     (genre, subgenre, "tm_search" if genre else None, now_iso(), cand["id"]))
    conn.commit()
    if any(counts.values()):
        log.info("Genre lookups for venue-calendar acts: %s", counts)
    return counts
