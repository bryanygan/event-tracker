"""Spotify enrichment: genres + popularity for headliners, used in notability scoring.

Uses the client-credentials flow (no user login). Each artist is looked up at most
once every REFRESH_DAYS, so normal runs only touch newly seen artists.
If Spotify stops returning a field (they have trimmed the API before), it's stored as
NULL and scoring falls back to venue size + tour size.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from .. import config
from ..store import now_iso

log = logging.getLogger(__name__)

REFRESH_DAYS = 30
MAX_LOOKUPS_PER_RUN = 150


class SpotifyClient:
    def __init__(self, client_id=None, client_secret=None, session=None):
        self.client_id = client_id or config.SPOTIFY_CLIENT_ID
        self.client_secret = client_secret or config.SPOTIFY_CLIENT_SECRET
        self.session = session or requests.Session()
        self._token, self._expires = None, 0.0
        self.calls = 0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _auth(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = self.session.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                              headers={"Authorization": f"Basic {basic}"}, timeout=20)
        r.raise_for_status()
        body = r.json()
        self._token, self._expires = body["access_token"], time.time() + body.get("expires_in", 3600)
        return self._token

    def find_artist(self, name: str) -> dict | None:
        """Best exact-name match from search, or None."""
        for attempt in range(3):
            self.calls += 1
            r = self.session.get("https://api.spotify.com/v1/search", timeout=20,
                                 params={"q": name, "type": "artist", "limit": 5},
                                 headers={"Authorization": f"Bearer {self._auth()}"})
            if r.status_code == 429:
                time.sleep(min(int(r.headers.get("Retry-After", 2)), 30))
                continue
            r.raise_for_status()
            items = r.json().get("artists", {}).get("items", [])
            target = name.casefold().strip()
            exact = [a for a in items if a.get("name", "").casefold().strip() == target]
            if not exact:
                return None
            # Several artists can share a name; prefer the most popular/followed one.
            return max(exact, key=lambda a: (a.get("popularity") or 0, (a.get("followers") or {}).get("total") or 0))
        return None


def enrich_headliners(conn, client: SpotifyClient | None = None, limit: int = MAX_LOOKUPS_PER_RUN) -> int:
    """Look up headliners that have upcoming shows and haven't been checked recently."""
    client = client or SpotifyClient()
    if not client.configured:
        log.info("Spotify not configured; skipping enrichment")
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT DISTINCT a.id, a.name FROM attractions a
           JOIN event_attractions ea ON ea.attraction_id = a.id AND ea.position = 0
           JOIN events e ON e.id = ea.event_id AND e.active = 1
           WHERE a.spotify_checked_at IS NULL OR a.spotify_checked_at < ?
           ORDER BY a.tm_upcoming_total DESC LIMIT ?""", (cutoff, limit)).fetchall()
    done = 0
    for row in rows:
        try:
            artist = client.find_artist(row["name"])
        except requests.RequestException as exc:
            log.warning("Spotify lookup failed for %s: %s", row["name"], exc)
            break
        conn.execute(
            """UPDATE attractions SET spotify_id=?, spotify_popularity=?, spotify_followers=?,
               spotify_genres=?, spotify_checked_at=? WHERE id=?""",
            (artist and artist.get("id"), artist and artist.get("popularity"),
             artist and (artist.get("followers") or {}).get("total"),
             artist and ", ".join(artist.get("genres") or []) or None, now_iso(), row["id"]))
        done += 1
    conn.commit()
    log.info("Spotify: enriched %d artists (%d calls)", done, client.calls)
    return done
