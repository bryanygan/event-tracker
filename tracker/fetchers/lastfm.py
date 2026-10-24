"""Last.fm's public API (read-only calls with LAST_FM_API_KEY; the shared secret is only for write/auth calls, which
we never make). Used by tracker/discovery.py (roadmap M7) for similar artists, niche tag lists, and daily listener counts.

Terms (checked 2026-10-23): identify the app with a User-Agent, keep the request rate reasonable (well under 5/s), cache
responses. Tag counts and similar-artist lists are candidate lists, not proof that an artist is taking off.
"""
from __future__ import annotations

import time

import requests

from .. import config

URL = "https://ws.audioscrobbler.com/2.0/"
MIN_SECONDS_BETWEEN_CALLS = 0.3


class LastfmError(requests.RequestException):
    pass


class LastfmClient:
    def __init__(self, session: requests.Session | None = None, api_key: str | None = None,
                 min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else config.LAST_FM_API_KEY
        self.min_interval = min_interval
        self.calls = 0
        self._last = 0.0

    def _get(self, method: str, **params) -> dict | None:
        if not self.api_key:
            raise LastfmError("LAST_FM_API_KEY isn't set in .env")
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        r = self.session.get(URL, timeout=20, headers={"User-Agent": config.NEWS_USER_AGENT},
                             params={"method": method, "api_key": self.api_key, "format": "json", **params})
        if r.status_code == 429:
            raise LastfmError("Last.fm rate limit")
        body = r.json() if r.content else {}
        if "error" in body:
            if body["error"] in (11, 16, 26, 29):  # service offline, temporary error, suspended key, rate limit
                raise LastfmError(f"Last.fm error {body['error']}: {body.get('message')}")
            return None  # 6 = no such artist / tag: a normal answer
        r.raise_for_status()
        return body

    def similar(self, artist: str, limit: int = 20) -> list[dict]:
        """[{name, match (0-1), mbid, url}] most similar first."""
        body = self._get("artist.getsimilar", artist=artist, limit=limit, autocorrect=1) or {}
        return [{"name": a["name"], "match": float(a.get("match") or 0), "mbid": a.get("mbid") or None,
                 "url": a.get("url")} for a in (body.get("similarartists") or {}).get("artist", [])]

    def tag_top(self, tag: str, limit: int = 50) -> list[dict]:
        """[{name, mbid, url, rank}] for a tag."""
        body = self._get("tag.gettopartists", tag=tag, limit=limit) or {}
        return [{"name": a["name"], "mbid": a.get("mbid") or None, "url": a.get("url"),
                 "rank": int((a.get("@attr") or {}).get("rank") or i + 1)}
                for i, a in enumerate((body.get("topartists") or {}).get("artist", []))]

    def info(self, artist: str) -> dict | None:
        """{name, mbid, url, listeners, playcount, tags} or None if Last.fm doesn't know them."""
        body = self._get("artist.getinfo", artist=artist, autocorrect=1)
        a = (body or {}).get("artist")
        if not a:
            return None
        stats = a.get("stats") or {}
        return {"name": a.get("name"), "mbid": a.get("mbid") or None, "url": a.get("url"),
                "listeners": int(stats["listeners"]) if stats.get("listeners") else None,
                "playcount": int(stats["playcount"]) if stats.get("playcount") else None,
                "tags": [t["name"] for t in (a.get("tags") or {}).get("tag", [])][:5]}
