"""Artist popularity from Deezer's public API (no key needed), used in notability scoring.

Why Deezer and not Spotify: Spotify's API no longer returns popularity, followers or
genres for development-mode apps (checked Oct 2026: only id/name/images come back, and
top-tracks is 403). Deezer's `nb_fan` is a usable stand-in. It skews a bit European and
undercounts some US acts (country especially), which is why scoring blends it with venue
size and tour size rather than relying on it alone.

Deezer allows ~50 requests per 5 seconds; we throttle well under that and only look up
headliners we haven't checked in REFRESH_DAYS. A matched artist costs one more call for their
releases, whose most common genre checks Ticketmaster's label (genres.py).
"""
from __future__ import annotations

import logging
import math
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from .. import config
from ..store import now_iso

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.deezer.com/search/artist"
ALBUMS_URL = "https://api.deezer.com/artist/{}/albums"
ARTIST_URL = "https://api.deezer.com/artist/{}"
GENRES_URL = "https://api.deezer.com/genre"
REFRESH_DAYS = 14
MAX_LOOKUPS_PER_RUN = 600  # covers every headliner on a first fill (~1.5 min)
MIN_SECONDS_BETWEEN_CALLS = 0.15


def popularity_from_fans(fans: int | None) -> int | None:
    """Log scale: 100 fans → 0, 10k → 40, 100k → 60, 1M → 80, 10M+ → 100."""
    if fans is None:
        return None
    return max(0, min(100, round(20 * (math.log10(max(fans, 1)) - 2))))


class DeezerClient:
    def __init__(self, session: requests.Session | None = None, min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self.session = session or requests.Session()
        self.min_interval = min_interval
        self.calls = 0
        self._last = 0.0
        self.genre_names: dict[int, str] | None = None  # Deezer genre id -> name, fetched once

    def _get(self, url: str, params: dict, what: str) -> dict | None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        r = self.session.get(url, params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        if "error" in body:  # Deezer reports errors with HTTP 200
            if body["error"].get("code") == 4:  # quota exceeded: stop for this run
                raise requests.RequestException(f"Deezer quota: {body['error']}")
            log.info("Deezer error for %r: %s", what, body["error"])
            return None
        return body

    def find_artist(self, name: str) -> dict | None:
        """Exact (case-insensitive) name match with the most fans, or None."""
        body = self._get(SEARCH_URL, {"q": name, "limit": 10}, name)
        if body is None:
            return None
        target = name.casefold().strip()
        exact = [a for a in body.get("data", []) if a.get("name", "").casefold().strip() == target]
        return max(exact, key=lambda a: a.get("nb_fan") or 0) if exact else None

    def fans(self, artist_id: int) -> int | None:
        """The artist's current fan count (momentum snapshots, tracker/discovery.py)."""
        body = self._get(ARTIST_URL.format(artist_id), {}, artist_id)
        return (body or {}).get("nb_fan")

    def releases(self, artist_id: int, limit: int = 50) -> list[dict]:
        """Newest releases (tracker/catalog.py): {provider_id, title, record_type, release_date, url}. Deezer lists
        a release on its day, never before (2026-10-23 bake-off)."""
        body = self._get(ALBUMS_URL.format(artist_id), {"limit": limit}, artist_id)
        return [{"provider_id": str(a["id"]), "title": a.get("title") or "", "record_type": a.get("record_type") or "",
                 "release_date": a["release_date"], "url": a.get("link")}
                for a in (body or {}).get("data", []) if a.get("release_date")]

    def artist_genre(self, artist_id: int) -> str | None:
        """The genre most of an artist's releases share, in Ticketmaster's words, or None when unclear."""
        body = self._get(ALBUMS_URL.format(artist_id), {"limit": 25}, artist_id)
        if body is None:
            return None
        if self.genre_names is None:
            names = self._get(GENRES_URL, {}, "genre list") or {}
            self.genre_names = {g["id"]: g["name"] for g in names.get("data", [])}
        return release_genre([self.genre_names.get(a.get("genre_id")) for a in body.get("data", [])])


def release_genre(deezer_names: list[str | None]) -> str | None:
    mapped = [config.DEEZER_GENRES[n] for n in deezer_names if n in config.DEEZER_GENRES]
    if len(mapped) < config.DEEZER_GENRE_MIN_RELEASES:
        return None
    genre, n = Counter(mapped).most_common(1)[0]
    return genre if n / len(mapped) >= config.DEEZER_GENRE_MIN_SHARE else None


def enrich_headliners(conn, client: DeezerClient | None = None, limit: int = MAX_LOOKUPS_PER_RUN) -> int:
    """Look up headliners with upcoming shows that haven't been checked recently."""
    client = client or DeezerClient()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT DISTINCT a.id, a.name, a.tm_upcoming_total FROM attractions a
           JOIN event_attractions ea ON ea.attraction_id = a.id AND ea.position = 0
           JOIN events e ON e.id = ea.event_id AND e.active = 1 AND e.segment IS NOT 'Sports'
           WHERE a.popularity_checked_at IS NULL OR a.popularity_checked_at < ? OR a.deezer_genre_checked_at IS NULL
           ORDER BY a.tm_upcoming_total DESC LIMIT ?""", (cutoff, limit)).fetchall()
    done = 0
    for row in rows:
        if not row["name"].strip():
            continue
        try:
            artist = client.find_artist(row["name"])
        except requests.RequestException as exc:
            log.warning("Deezer lookup failed for %s: %s (stopping for this run)", row["name"], exc)
            break
        fans = artist.get("nb_fan") if artist else None
        conn.execute("UPDATE attractions SET fans=?, popularity=?, popularity_checked_at=? WHERE id=?",
                     (fans, popularity_from_fans(fans), now_iso(), row["id"]))
        done += 1
        try:
            genre = client.artist_genre(artist["id"]) if artist and artist.get("id") else None
        except requests.RequestException as exc:  # keep the fans; the genre is retried next run
            log.warning("Deezer genre lookup failed for %s: %s (stopping for this run)", row["name"], exc)
            break
        conn.execute("UPDATE attractions SET deezer_genre=?, deezer_genre_checked_at=? WHERE id=?",
                     (genre, now_iso(), row["id"]))
    conn.commit()
    log.info("Deezer: checked %d artists (%d calls)", done, client.calls)
    return done
