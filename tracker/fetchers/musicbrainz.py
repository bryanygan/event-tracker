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


class MusicBrainzClient:
    def __init__(self, session: requests.Session | None = None, min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self.session = session or requests.Session()
        self.min_interval = min_interval
        self.calls = 0
        self._last = 0.0

    def find_artist(self, name: str) -> tuple[str | None, bool]:
        """(mbid, ambiguous). An id only for exactly one strong candidate with this exact name (case-insensitive,
        or one of its aliases); two or more such candidates = ambiguous (e.g. several artists called "Tomorrow")."""
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        phrase = name.replace("\\", "\\\\").replace('"', '\\"')
        r = self.session.get(SEARCH_URL, params={"query": f'artist:"{phrase}"', "fmt": "json", "limit": 10},
                             headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
        if r.status_code == 503:  # rate limited: stop for this run
            raise requests.RequestException("MusicBrainz rate limit (503)")
        r.raise_for_status()
        target = name.casefold().strip()

        def names(a: dict) -> set[str]:
            return {a.get("name", "").casefold().strip()} | {x.get("name", "").casefold().strip()
                                                             for x in a.get("aliases") or []}

        exact = [a for a in r.json().get("artists", []) if int(a.get("score", 0)) >= MIN_SCORE and target in names(a)]
        if len(exact) == 1:
            return exact[0]["id"], False
        return None, len(exact) > 1
