"""Notability score (0-100) for an event, so drafts focus on shows people care about.

Blend of three signals, each 0-100:
  - Headliner popularity from Deezer fan counts (best signal, when we have it)
  - Venue tier (a stadium show is a bigger deal than a 300-cap room)
  - Tour size: how many events Ticketmaster lists for the headliner worldwide
If popularity is unknown, its weight is redistributed to the other two.
Tweak the weights here; everything downstream just reads `score`.
"""
from __future__ import annotations

import math
from datetime import date

from . import config, listening

WEIGHTS = {"popularity": 0.5, "venue": 0.3, "tour": 0.2}


def tour_signal(upcoming_total: int | None) -> float:
    # 1 show → ~0, 10 shows → ~50, 100+ shows → 100 (log scale)
    if not upcoming_total:
        return 0.0
    return min(100.0, 50.0 * math.log10(max(upcoming_total, 1)))


def score(popularity: int | None, venue_tier: int | None, tm_upcoming_total: int | None) -> float:
    parts = {
        "venue": float(venue_tier or 0),
        "tour": tour_signal(tm_upcoming_total),
    }
    if popularity is not None:
        parts["popularity"] = float(popularity)
    total_weight = sum(WEIGHTS[k] for k in parts)
    return round(sum(WEIGHTS[k] * v for k, v in parts.items()) / total_weight, 1)


# SQL that returns every upcoming active event with its headliner, venue and score inputs.
# Detectors and the dashboard both use this so "notable" means the same thing everywhere.
EVENT_VIEW_SQL = """
SELECT e.*, v.name AS venue_name, v.city AS venue_city, v.state AS venue_state, v.tier AS venue_tier,
       a.id AS headliner_id, a.name AS headliner, a.popularity, a.fans,
       a.last_area_show_date, a.last_area_show_venue, a.last_area_show_acts,
       a.tm_upcoming_total,
       (SELECT group_concat(a2.name, ', ') FROM event_attractions ea2
          JOIN attractions a2 ON a2.id = ea2.attraction_id
          WHERE ea2.event_id = e.id AND ea2.position > 0) AS support
FROM events e
LEFT JOIN venues v ON v.id = e.venue_id
LEFT JOIN event_attractions ea ON ea.event_id = e.id AND ea.position = 0
LEFT JOIN attractions a ON a.id = ea.attraction_id
WHERE e.active = 1 AND e.local_date >= ?
"""


def is_game(e: dict) -> bool:
    return e.get("segment") == "Sports"


def game_score(opponent: str | None, local_date: str) -> float:
    """Home games: base + marquee opponent + weekend. (Fan counts mean nothing for a team.)"""
    s = config.SPORTS_BASE_SCORE
    if opponent in config.MARQUEE_OPPONENTS:
        s += config.MARQUEE_BONUS
    if date.fromisoformat(local_date).weekday() >= 4:
        s += config.WEEKEND_GAME_BONUS
    return float(min(s, 100))


def upcoming_events(conn, today: str) -> list[dict]:
    """Upcoming events as dicts with a computed `score`, highest first."""
    rows = [dict(r) for r in conn.execute(EVENT_VIEW_SQL + " ORDER BY e.local_date", (today,))]
    taste = listening.load()
    for r in rows:
        r["headliner"] = r["headliner"] or r["name"]
        if is_game(r):
            r["opponent"] = (r["support"] or "").split(", ")[0] or None  # home team first, opponent second
            r["score"] = game_score(r["opponent"], r["local_date"])
            r["my_plays"], r["niche"], r["taste_bonus"] = 0, False, 0
            continue
        base = score(r["popularity"], r["venue_tier"], r["tm_upcoming_total"])
        # Your taste: plays of the headliner or anyone on the bill, plus your genre lane. The genre bucket also
        # fills in for venue-calendar shows, which come without a genre.
        r["my_plays"] = taste.plays_for(r["headliner"], *(r["support"] or "").split(", "))
        r["my_bucket"] = taste.bucket_for(r["headliner"])
        r["niche"] = listening.in_niche(r["genre"], r["subgenre"], r["my_bucket"])
        r["taste_bonus"] = listening.plays_bonus(r["my_plays"]) + (config.NICHE_GENRE_BONUS if r["niche"] else 0)
        r["score"] = min(100.0, round(base + r["taste_bonus"], 1))
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows
