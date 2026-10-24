"""ListenBrainz's public stats (no key) for momentum snapshots (tracker/discovery.py, roadmap M7). All-time listen and
listener counts by MusicBrainz id; the daily change is the signal. Coverage of underground artists varies, so it's shown
as supplementary and never counts toward the two-signal rule. The API sends X-RateLimit headers; we go far slower.
"""
from __future__ import annotations

import time

import requests

from .. import config

URL = "https://api.listenbrainz.org/1/stats/artist/{}/listeners"
MIN_SECONDS_BETWEEN_CALLS = 0.5


class ListenBrainzClient:
    def __init__(self, session: requests.Session | None = None, min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self.session = session or requests.Session()
        self.min_interval = min_interval
        self.calls = 0
        self._last = 0.0

    def listens(self, mbid: str) -> dict | None:
        """{listens, listeners} all time, or None (no stats yet, or an unknown id)."""
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        r = self.session.get(URL.format(mbid), timeout=20, headers={"User-Agent": config.NEWS_USER_AGENT})
        if r.status_code in (204, 404):
            return None
        if r.status_code == 429:
            raise requests.RequestException("ListenBrainz rate limit")
        r.raise_for_status()
        p = (r.json() or {}).get("payload") or {}
        if p.get("total_listen_count") is None:
            return None
        return {"listens": int(p["total_listen_count"]), "listeners": int(p.get("total_user_count") or 0)}
