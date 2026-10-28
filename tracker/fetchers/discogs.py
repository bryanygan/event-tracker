"""Your record collection from the Discogs API (tracker/vinyl.py).

Needs DISCOGS_TOKEN in .env (a personal access token from Discogs Settings → Developers). The token says whose
collection it is (/oauth/identity), so no username lives in the repo. Authenticated calls get 60 requests a
minute; we stay under that and back off when Discogs says so (429). Only reads: nothing is ever changed on
Discogs.
"""
from __future__ import annotations

import logging
import time

import requests

from .. import config

log = logging.getLogger(__name__)

API = "https://api.discogs.com"
UA = "PhillyEventsTracker/0.1 +https://github.com/bryanygan/event-tracker"
MIN_SECONDS_BETWEEN_CALLS = 1.1
RETRY_WAITS = (10, 30, 60)


class DiscogsError(RuntimeError):
    pass


class DiscogsClient:
    def __init__(self, token: str | None = None, session: requests.Session | None = None,
                 min_interval: float = MIN_SECONDS_BETWEEN_CALLS, retry_waits: tuple[float, ...] = RETRY_WAITS):
        self.token = token if token is not None else config.DISCOGS_TOKEN
        if not self.token:
            raise DiscogsError("DISCOGS_TOKEN is missing from .env")
        self.session = session or requests.Session()
        self.min_interval = min_interval
        self.retry_waits = retry_waits
        self.calls = 0
        self._last = 0.0
        self._user: str | None = None

    def _get(self, path: str, params: dict | None = None, ok_missing: bool = False) -> dict | None:
        for attempt in range(len(self.retry_waits) + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            r = self.session.get(API + path, params=params or {}, timeout=30,
                                 headers={"User-Agent": UA, "Authorization": f"Discogs token={self.token}"})
            if r.status_code == 429 and attempt < len(self.retry_waits):
                log.info("Discogs asked us to slow down; waiting %ss", self.retry_waits[attempt])
                time.sleep(self.retry_waits[attempt])
                continue
            if r.status_code == 404 and ok_missing:
                return None
            if r.status_code == 401:
                raise DiscogsError("Discogs refused the token (401): check DISCOGS_TOKEN in .env")
            r.raise_for_status()
            return r.json()
        raise DiscogsError("Discogs rate limit (429) didn't clear")

    def username(self) -> str:
        if self._user is None:
            self._user = self._get("/oauth/identity")["username"]
        return self._user

    def collection(self) -> list[dict]:
        """Every release in your collection (folder 0 = all), newest first. 100 per call."""
        out, page = [], 1
        while True:
            body = self._get(f"/users/{self.username()}/collection/folders/0/releases",
                             {"per_page": 100, "page": page, "sort": "added", "sort_order": "desc"})
            out += body["releases"]
            if page >= body["pagination"]["pages"]:
                return out
            page += 1

    def collection_value(self) -> dict:
        """Discogs' estimate of the whole collection: {"minimum", "median", "maximum"} as "$1,234.56" strings."""
        return self._get(f"/users/{self.username()}/collection/value")

    def release(self, release_id: int) -> dict | None:
        return self._get(f"/releases/{release_id}", {"curr_abbr": "USD"}, ok_missing=True)

    def master(self, master_id: int) -> dict | None:
        return self._get(f"/masters/{master_id}", ok_missing=True)

    def price_suggestions(self, release_id: int) -> dict | None:
        """Discogs' suggested price by condition ({"Near Mint (NM or M-)": {"value", "currency"}, ...}), from
        recent sales. Empty when there's no sales history."""
        return self._get(f"/marketplace/price_suggestions/{release_id}", ok_missing=True)

    def search_vinyl(self, artist: str, title: str | None = None, per_page: int = 10) -> list[dict]:
        """Discogs database search for vinyl masters by an artist (and title)."""
        params = {"artist": artist, "format": "Vinyl", "type": "master", "per_page": per_page}
        if title:
            params["release_title"] = title
        return (self._get("/database/search", params) or {}).get("results", [])
