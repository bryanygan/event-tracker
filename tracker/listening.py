"""Your own listening history as a taste signal: who you play most, and which genre lane an artist is in.

Source: the ttspotslideshow project's plays.db (Spotify play history + per-artist genre buckets built from
Spotify genres and Last.fm tags), opened READ-ONLY. Path comes from LISTENING_DB in .env; if it's unset or
missing, everything here returns empty and scoring is unchanged.

This is personal data and the repo is public: nothing from it is ever written into the repo or into
alert facts the LLM sees. It only adjusts scores and shows as a 🎧 badge in the (private) dashboard.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


def artist_key(name: str | None) -> str:
    """Loose match key: "O'Neil" == "O’Neil" == "oneil"; keeps $ and ¥ (A$AP, ¥$)."""
    return re.sub(r"[^\w$¥]", "", (name or "").casefold()).replace("_", "")


@dataclass
class Taste:
    plays: dict[str, int] = field(default_factory=dict)     # artist_key -> plays in the window
    buckets: dict[str, str] = field(default_factory=dict)   # artist_key -> genre bucket ("rage", "pop", ...)
    names: dict[str, str] = field(default_factory=dict)     # artist_key -> most-played spelling (the watchlist's name)

    def plays_for(self, *names: str | None) -> int:
        return max((self.plays.get(artist_key(n), 0) for n in names if n), default=0)

    def bucket_for(self, name: str | None) -> str | None:
        return self.buckets.get(artist_key(name))


_cache: dict[str, Taste] = {}


def load(path: str | None = None, today: date | None = None) -> Taste:
    """Read plays.db once per process (a few hundred ms for a large history)."""
    path = path if path is not None else config.LISTENING_DB
    if not path or not Path(path).exists():
        return Taste()
    if path in _cache:
        return _cache[path]
    since = ((today or date.today()) - timedelta(days=config.LISTENING_WINDOW_DAYS)).isoformat()
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        taste = Taste()
        best: dict[str, int] = {}
        for artist, n in con.execute("SELECT artist, COUNT(*) FROM plays WHERE played_at >= ? GROUP BY artist", (since,)):
            k = artist_key(artist)
            if not k:
                continue
            taste.plays[k] = taste.plays.get(k, 0) + n  # "artist" and "Artist" are one artist
            if n > best.get(k, 0):
                best[k], taste.names[k] = n, artist.strip()
        for key, bucket in con.execute("SELECT artist_key, primary_bucket FROM artist_genres"):
            taste.buckets.setdefault(artist_key(key), bucket)
        con.close()
    except sqlite3.Error as exc:
        log.warning("Couldn't read listening history at %s: %s", path, exc)
        return Taste()
    log.info("Listening history: %d artists played in the last %d days", len(taste.plays), config.LISTENING_WINDOW_DAYS)
    _cache[path] = taste
    return taste


def rotation(taste: Taste, min_plays: int | None = None) -> dict[str, tuple[str, int]]:
    """artist_key -> (name, plays) for artists with at least `min_plays` (default ROTATION_MIN_PLAYS)."""
    floor = config.ROTATION_MIN_PLAYS if min_plays is None else min_plays
    return {k: (taste.names.get(k, k), n) for k, n in taste.plays.items() if n >= floor}


def plays_bonus(plays: int) -> int:
    for minimum, bonus in config.PLAYS_BONUS:
        if plays >= minimum:
            return bonus
    return 0


def in_niche(genre: str | None, subgenre: str | None, bucket: str | None) -> bool:
    return (genre in config.NICHE_TM_GENRES or subgenre in config.NICHE_TM_SUBGENRES
            or (bucket or "") in config.NICHE_BUCKETS)
