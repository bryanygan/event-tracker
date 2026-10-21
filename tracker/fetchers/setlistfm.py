"""Philly show history from setlist.fm, for the "first Philly show in N years" angle.

API docs: https://api.setlist.fm/docs/1.0/index.html (free key, non-commercial use; credit setlist.fm
when its data is shown). Limits: ~2 requests/second and 1,440/day, so we throttle, cap lookups per run,
and cache results for REFRESH_DAYS.

How "Philly area" is decided: setlist.fm's cityName search is fuzzy (cityName=Philadelphia also returns
Camden, NJ and Chester, PA), so every result is filtered by venue coordinates with the same radius the
Ticketmaster search uses. First version paged through the artist's last 100 setlists, which for heavy
tourers only reached back ~18 months; searching artist+city gets their whole Philly history in one call.

setlist.fm is crowd-sourced: a show that wasn't logged won't be found. So we only ever use a *found*
date ("last Philly show: Nov 2019"); an artist with no Philly show on record is never called a first-timer.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timedelta, timezone

import requests

from .. import config
from ..store import now_iso

log = logging.getLogger(__name__)

BASE = "https://api.setlist.fm/rest/1.0"
REFRESH_DAYS = 30
MAX_ARTISTS_PER_RUN = 100  # ~2-3 calls each → well under the 1,440/day limit at 2 runs/day
MIN_SECONDS_BETWEEN_CALLS = 0.6   # stay under 2 requests/second


def miles_between(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8  # Earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_HOME = tuple(float(x) for x in config.LATLONG.split(","))


def in_area(coords: dict | None) -> bool:
    if not coords or coords.get("lat") is None or coords.get("long") is None:
        return False
    return miles_between(_HOME[0], _HOME[1], coords["lat"], coords["long"]) <= config.RADIUS_MILES


class SetlistFmClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None,
                 min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self.api_key = api_key if api_key is not None else config.SETLISTFM_API_KEY
        self.session = session or requests.Session()
        self.min_interval = min_interval
        self.calls = 0
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict) -> dict | None:
        """GET → JSON, or None for 404 (setlist.fm answers 'no results' with a 404)."""
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            r = self.session.get(f"{BASE}{path}", params=params, timeout=20,
                                 headers={"x-api-key": self.api_key, "Accept": "application/json"})
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise requests.RequestException("setlist.fm kept returning 429 (rate limited)")

    def find_mbid(self, name: str) -> str | None:
        """MusicBrainz id of the artist with exactly this name (case-insensitive), or None."""
        body = self._get("/search/artists", {"artistName": name, "sort": "relevance"})
        target = name.casefold().strip()
        for a in (body or {}).get("artist", []):
            if a.get("name", "").casefold().strip() == target:
                return a["mbid"]
        return None

    def last_area_show(self, mbid: str, today: date) -> dict:
        """Most recent past show within the radius, from two queries (2 API calls):
          1. search/setlists?artistMbid&cityName=Philadelphia: the artist's whole Philly history in one call
             (the fuzzy city match also returns Camden/Chester, which is what we want);
          2. the artist's latest setlists, to catch area venues outside that match (Glenside, Bensalem...).
        Both are filtered by venue coordinates. Returns {"date", "venue", "checked_back_to"}."""
        candidates, oldest = [], None
        for path, params in (("/search/setlists", {"artistMbid": mbid, "cityName": "Philadelphia", "p": 1}),
                             (f"/artist/{mbid}/setlists", {"p": 1})):
            for s in (self._get(path, params) or {}).get("setlist", []):
                try:
                    d = datetime.strptime(s["eventDate"], "%d-%m-%Y").date()
                except (KeyError, ValueError):
                    continue
                if d >= today:  # upcoming shows are listed too; we want the last *past* one
                    continue
                oldest = d if oldest is None else min(oldest, d)
                venue = s.get("venue") or {}
                if in_area(venue.get("city", {}).get("coords")):
                    candidates.append((d, venue.get("name")))
        if candidates:
            d, venue = max(candidates)
            return {"date": d, "venue": venue, "checked_back_to": oldest}
        return {"date": None, "venue": None, "checked_back_to": oldest}


def enrich_history(conn, client: SetlistFmClient | None = None, limit: int = MAX_ARTISTS_PER_RUN,
                   today: date | None = None) -> int:
    """Look up Philly history for music headliners with upcoming shows, newest-announced first."""
    client = client or SetlistFmClient()
    if not client.configured:
        log.info("setlist.fm not configured; skipping history")
        return 0
    today = today or date.today()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT DISTINCT a.id, a.name, a.setlistfm_mbid, a.first_seen, a.popularity FROM attractions a
           JOIN event_attractions ea ON ea.attraction_id = a.id AND ea.position = 0
           JOIN events e ON e.id = ea.event_id AND e.active = 1 AND e.segment IS NOT 'Sports'
           WHERE a.history_checked_at IS NULL OR a.history_checked_at < ?
           ORDER BY a.first_seen DESC, a.popularity DESC LIMIT ?""", (cutoff, limit)).fetchall()
    done = 0
    for row in rows:
        try:
            mbid = row["setlistfm_mbid"] or client.find_mbid(row["name"])
            found = client.last_area_show(mbid, today) if mbid else {"date": None, "venue": None, "checked_back_to": None}
        except requests.RequestException as exc:
            log.warning("setlist.fm lookup failed for %s: %s (stopping for this run)", row["name"], exc)
            break
        conn.execute(
            """UPDATE attractions SET setlistfm_mbid=?, last_area_show_date=?, last_area_show_venue=?,
               history_checked_back_to=?, history_checked_at=? WHERE id=?""",
            (mbid, found["date"] and found["date"].isoformat(), found["venue"],
             found["checked_back_to"] and found["checked_back_to"].isoformat(), now_iso(), row["id"]))
        done += 1
    conn.commit()
    log.info("setlist.fm: checked %d artists (%d calls)", done, client.calls)
    return done
