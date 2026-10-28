"""MusicBrainz artist ids for the music watchlist (tracker/watchlist.py; roadmap M2).

MusicBrainz asks for at most one request per second and a User-Agent that says who's calling
(https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting). No key needed.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

SEARCH_URL = "https://musicbrainz.org/ws/2/artist"
UA = "PhillyEventsTracker/0.1 ( https://github.com/bryanygan/event-tracker )"
MIN_SECONDS_BETWEEN_CALLS = 1.1
MIN_SCORE = 90  # MusicBrainz's own 0-100 search score
# MusicBrainz also answers 503 when its servers shed load (Retry-After: 0, quota left; seen on ~1 in 3 requests,
# 2026-10-24), not only when we're too fast. Wait and retry a few times before giving up for this run.
RETRY_WAITS = (2, 5, 10)

# url-rels types worth reading (https://musicbrainz.org/relationships/artist-url)
LINK_TYPES = {"official homepage": "homepage", "youtube": "youtube", "blog": "blog"}


class MusicBrainzClient:
    def __init__(self, session: requests.Session | None = None, min_interval: float = MIN_SECONDS_BETWEEN_CALLS,
                 retry_waits: tuple[float, ...] = RETRY_WAITS):
        self.session = session or requests.Session()
        self.min_interval = min_interval
        self.retry_waits = retry_waits
        self.calls = 0
        self._last = 0.0

    def _get(self, url: str, params: dict) -> requests.Response:
        """One polite GET: spaced min_interval apart, retried on 503. Raises RequestException if it stays 503."""
        for attempt in range(len(self.retry_waits) + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            r = self.session.get(url, params={**params, "fmt": "json"},
                                 headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
            if r.status_code != 503:
                r.raise_for_status()
                return r
            if attempt < len(self.retry_waits):
                retry_after = r.headers.get("Retry-After", "")
                time.sleep(max(self.retry_waits[attempt], int(retry_after) if retry_after.isdigit() else 0))
        raise requests.RequestException("MusicBrainz rate limit (503)")

    def links(self, mbid: str) -> list[dict]:
        """The artist's own pages MusicBrainz lists: [{"type": homepage|youtube|blog, "url"}], ended links skipped."""
        r = self._get(f"{SEARCH_URL}/{mbid}", {"inc": "url-rels"})
        out = []
        for rel in r.json().get("relations") or []:
            kind = LINK_TYPES.get(rel.get("type"))
            url = (rel.get("url") or {}).get("resource")
            if kind and url and not rel.get("ended"):
                out.append({"type": kind, "url": url})
        return out

    def find_artist(self, name: str) -> tuple[str | None, bool]:
        """(mbid, ambiguous). An id only for exactly one strong candidate with this exact name (case-insensitive,
        or one of its aliases); two or more such candidates = ambiguous (e.g. several artists called "Tomorrow")."""
        phrase = name.replace("\\", "\\\\").replace('"', '\\"')
        r = self._get(SEARCH_URL, {"query": f'artist:"{phrase}"', "limit": 10})
        target = name.casefold().strip()

        def names(a: dict) -> set[str]:
            return {a.get("name", "").casefold().strip()} | {x.get("name", "").casefold().strip()
                                                             for x in a.get("aliases") or []}

        exact = [a for a in r.json().get("artists", []) if int(a.get("score", 0)) >= MIN_SCORE and target in names(a)]
        if len(exact) == 1:
            return exact[0]["id"], False
        return None, len(exact) > 1

    def find_release_group(self, artist: str, title: str) -> dict | None:
        """The album (release group) by this artist with this title, for its first release date (vinyl.py).
        Only an exact title match (ignoring case and punctuation) with a strong score; else None.
        Returns {"id", "title", "first_release_date", "primary_type"}."""
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')
        r = self._get("https://musicbrainz.org/ws/2/release-group",
                      {"query": f'releasegroup:"{esc(title)}" AND artist:"{esc(artist)}"', "limit": 5})
        from ..search import fold
        for g in r.json().get("release-groups", []):
            credit = " ".join(c.get("name", "") + c.get("joinphrase", "") for c in g.get("artist-credit") or [])
            if (int(g.get("score", 0)) >= MIN_SCORE and fold(g.get("title")) == fold(title)
                    and fold(artist.split(",")[0]) in fold(credit)):
                return {"id": g["id"], "title": g["title"], "first_release_date": g.get("first-release-date") or None,
                        "primary_type": g.get("primary-type")}
        return None
