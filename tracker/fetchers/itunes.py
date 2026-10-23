"""iTunes Search / Lookup API (free, no key): release catalogs for the music watchlist (tracker/catalog.py, roadmap
M4). Chosen in the 2026-10-23 bake-off for listing pre-orders (announced albums with their date) as well as
same-day releases. The free stand-in for the Apple Music API, which needs a paid developer membership.

Apple asks for about 20 calls a minute at most, so calls are spaced MIN_SECONDS_BETWEEN_CALLS apart. US storefront.
"""
from __future__ import annotations

import logging
import re
import time

import requests

log = logging.getLogger(__name__)

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
MIN_SECONDS_BETWEEN_CALLS = 3.1
COUNTRY = "US"


class ItunesClient:
    def __init__(self, session: requests.Session | None = None, min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self.session = session or requests.Session()
        self.min_interval = min_interval
        self.calls = 0
        self._last = 0.0

    def _get(self, url: str, params: dict) -> dict:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        r = self.session.get(url, params={**params, "country": COUNTRY}, timeout=20)
        if r.status_code in (403, 429):  # throttled: stop for this run
            raise requests.RequestException(f"iTunes rate limit ({r.status_code})")
        r.raise_for_status()
        return r.json()

    def find_artist(self, name: str) -> int | None:
        """The artist id for an exact (case-insensitive) name match, or None."""
        body = self._get(SEARCH_URL, {"term": name, "entity": "musicArtist", "limit": 10})
        target = name.casefold().strip()
        exact = [a for a in body.get("results", []) if a.get("artistName", "").casefold().strip() == target]
        return exact[0]["artistId"] if exact else None

    def releases(self, artist_id: int, limit: int = 50) -> list[dict]:
        """Newest albums, EPs and singles: {provider_id, title, record_type, release_date, url}."""
        body = self._get(LOOKUP_URL, {"id": artist_id, "entity": "album", "limit": limit, "sort": "recent"})
        out = []
        for c in body.get("results", []):
            if c.get("wrapperType") != "collection" or not c.get("releaseDate"):
                continue
            out.append({"provider_id": str(c["collectionId"]), "title": c.get("collectionName") or "",
                        "record_type": record_type(c.get("collectionName") or "", c.get("trackCount")),
                        "release_date": c["releaseDate"][:10],  # midnight Pacific, written in UTC: same day
                        "url": c.get("collectionViewUrl")})
        return out


def record_type(name: str, tracks: int | None) -> str:
    """iTunes calls everything a collection; the name says "- Single" / "- EP"."""
    if re.search(r"\s-\s*single$", name, re.I):
        return "single"
    if re.search(r"\s-\s*ep$", name, re.I):
        return "ep"
    return "single" if (tracks or 0) <= 3 else "album"
