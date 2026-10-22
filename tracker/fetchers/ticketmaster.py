"""Ticketmaster Discovery API v2 fetcher.

Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/

Two quirks shape this code:
1. Deep paging is capped: page * size must stay under 1000. So we query in date
   windows and split any window that reports more than ~950 results.
2. The free tier allows 5 calls/sec and 5000/day. We throttle and keep our own budget.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from .. import config

log = logging.getLogger(__name__)

BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
ATTRACTIONS_URL = "https://app.ticketmaster.com/discovery/v2/attractions.json"
PAGE_SIZE = 200
MAX_RESULTS_PER_QUERY = 950  # stay below TM's 1000 deep-paging cap

_JUNK = [re.compile(p, re.I) for p in config.JUNK_EVENT_PATTERNS]

# Age-limit boilerplate TM venues append to event names, e.g. "(21+ Event)",
# "(21 and Over)", "- All Ages (under 16 with adult)". Not something we want read out in a script.
_NAME_NOISE = re.compile(
    r"\s*\((?:\d\d\s*(?:\+|and over|& over)|all ages)[^)]*\)"  # (21+ Event), (18+), (21 and Over), (All Ages)
    r"|\s*[-–]\s*all ages\b(?:\s*\([^)]*\))?",      # - All Ages (under 16 with adult)
    re.I,
)


def clean_text(s: str | None) -> str | None:
    """Normalize whitespace (TM names often contain non-breaking spaces)."""
    if s is None:
        return None
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def clean_event_name(s: str | None) -> str:
    s = _NAME_NOISE.sub("", clean_text(s) or "")
    return re.sub(r"\s*[-–:]\s*$", "", s).strip()


class BudgetExceeded(RuntimeError):
    pass


class TicketmasterClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None,
                 budget: int = config.TM_DAILY_CALL_BUDGET, min_interval: float = config.TM_MIN_SECONDS_BETWEEN_CALLS):
        self.api_key = api_key or config.TICKETMASTER_API_KEY
        if not self.api_key:
            raise RuntimeError("TICKETMASTER_CONSUMER_KEY is missing. Add it to .env")
        self.session = session or requests.Session()
        self.budget = budget
        self.min_interval = min_interval
        self.calls = 0
        self._last_call = 0.0

    def _get(self, params: dict, url: str = BASE_URL) -> dict:
        if self.calls >= self.budget:
            raise BudgetExceeded(f"Hit self-imposed budget of {self.budget} calls")
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            self.calls += 1
            resp = self.session.get(url, params={**params, "apikey": self.api_key}, timeout=30)
            if resp.status_code == 429:  # rate limited: back off and retry
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("Ticketmaster kept returning 429 (rate limited)")

    def _query_window(self, start: datetime, end: datetime, classification: str,
                      truncate: bool = False) -> tuple[list[dict], int]:
        """All raw events in [start, end). Returns (events, total_reported).

        If the window is too big to page through, returns no events (so the caller
        can split it) unless `truncate` is set, in which case it takes what it can.
        """
        base = {
            "latlong": config.LATLONG,
            "radius": config.RADIUS_MILES,
            "unit": "miles",
            "classificationName": classification,
            "startDateTime": _tm_time(start),
            "endDateTime": _tm_time(end),
            "size": PAGE_SIZE,
            "sort": "date,asc",
            "locale": "*",
        }
        events, page, total = [], 0, 0
        while True:
            data = self._get({**base, "page": page})
            info = data.get("page", {})
            total = info.get("totalElements", 0)
            if total > MAX_RESULTS_PER_QUERY and not truncate:
                return [], total  # caller will split the window
            events.extend(data.get("_embedded", {}).get("events", []))
            page += 1
            if page >= info.get("totalPages", 0) or (page + 1) * PAGE_SIZE > MAX_RESULTS_PER_QUERY:
                return events, total

    def fetch_events(self, classification: str = "music", days: int = config.LOOKAHEAD_DAYS,
                     now: datetime | None = None) -> list[dict]:
        """Fetch every event in the area for the next `days`, splitting windows as needed."""
        now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        pending = [(now, now + timedelta(days=days))]
        found: dict[str, dict] = {}
        while pending:
            start, end = pending.pop(0)
            events, total = self._query_window(start, end, classification)
            if total > MAX_RESULTS_PER_QUERY:
                if end - start <= timedelta(hours=12):
                    log.warning("Window %s..%s still has %s events; taking first page only", start, end, total)
                    events, _ = self._query_window(start, end, classification, truncate=True)
                else:
                    mid = start + (end - start) / 2
                    pending[0:0] = [(start, mid), (mid, end)]
                    continue
            for ev in events:
                found[ev["id"]] = ev
            log.info("TM window %s → %s: %d events", start.date(), end.date(), len(events))
        return list(found.values())


    def find_attraction(self, name: str) -> tuple[str, dict | None]:
        """Ticketmaster's music attraction with exactly this name (case-insensitive).

        Returns ("found", attraction), ("none", None) or ("ambiguous", None). Two exact-name attractions
        means two different acts share the name, so we can't tell which one is playing: ambiguous."""
        data = self._get({"keyword": name, "classificationName": "music", "size": 20, "locale": "*"}, ATTRACTIONS_URL)
        target = (clean_text(name) or "").casefold()
        exact = [a for a in data.get("_embedded", {}).get("attractions") or []
                 if (clean_text(a.get("name")) or "").casefold() == target]
        if not exact:
            return "none", None
        if len(exact) > 1:
            return "ambiguous", None
        return "found", exact[0]


def attraction_genre(a: dict) -> tuple[str | None, str | None]:
    """(genre, subgenre) from a raw TM attraction, with placeholder labels dropped."""
    cls = (a.get("classifications") or [{}])[0]
    def name(key):
        n = clean_text((cls.get(key) or {}).get("name"))
        return None if n in (None, *config.NON_GENRES) else n
    return name("genre"), name("subGenre")


def _tm_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_tracked_team_event(raw: dict) -> bool:
    """Sports feeds are broad (college, G League...); keep games whose headliner is in SPORTS_TEAMS.
    The home team is listed first in Ticketmaster's attractions, so this keeps home games only."""
    attractions = raw.get("_embedded", {}).get("attractions") or []
    return bool(attractions) and clean_text(attractions[0].get("name")) in config.SPORTS_TEAMS


def is_junk(raw: dict) -> bool:
    name = raw.get("name", "")
    return any(p.search(name) for p in _JUNK)


def normalize(raw: dict) -> dict:
    """Flatten a raw TM event into the fields we store. Missing data stays None."""
    dates = raw.get("dates", {})
    start = dates.get("start", {})
    cls = (raw.get("classifications") or [{}])[0]
    prices = raw.get("priceRanges") or []
    sales = raw.get("sales", {})
    public = sales.get("public", {})
    emb = raw.get("_embedded", {})
    venue = (emb.get("venues") or [{}])[0]

    def cname(key):
        name = (cls.get(key) or {}).get("name")
        return None if name in (None, "Undefined", "Other") else name

    images = sorted(raw.get("images") or [], key=lambda i: i.get("width", 0), reverse=True)
    image = next((i["url"] for i in images if i.get("ratio") == "16_9"), images[0]["url"] if images else None)

    return {
        "id": raw["id"],
        "name": clean_event_name(raw.get("name")),
        "url": raw.get("url"),
        "image_url": image,
        "local_date": start.get("localDate"),
        "local_time": None if start.get("timeTBA") or start.get("noSpecificTime") else start.get("localTime"),
        "status": (dates.get("status") or {}).get("code"),
        "segment": cname("segment"),
        "genre": cname("genre"),
        "subgenre": cname("subGenre"),
        "price_min": min((p["min"] for p in prices if p.get("min") is not None), default=None),
        "price_max": max((p["max"] for p in prices if p.get("max") is not None), default=None),
        "currency": prices[0].get("currency") if prices else None,
        "onsale_start": public.get("startDateTime"),
        "onsale_end": public.get("endDateTime"),
        "venue": {
            "id": venue.get("id"),
            "name": clean_text(venue.get("name")),
            "city": (venue.get("city") or {}).get("name"),
            "state": (venue.get("state") or {}).get("stateCode"),
        } if venue.get("id") else None,
        "attractions": [
            {
                "id": a["id"],
                "name": clean_text(a.get("name")) or "",
                "genre": ((a.get("classifications") or [{}])[0].get("genre") or {}).get("name"),
                "subgenre": ((a.get("classifications") or [{}])[0].get("subGenre") or {}).get("name"),
                "tm_upcoming_total": (a.get("upcomingEvents") or {}).get("_total"),
            }
            for a in emb.get("attractions") or [] if a.get("id") and clean_text(a.get("name"))
        ],
        "presales": [
            {"name": clean_text(p.get("name")) or "Presale", "start_utc": p.get("startDateTime"), "end_utc": p.get("endDateTime")}
            for p in sales.get("presales") or []
        ],
    }


def save_raw(events: list[dict], path) -> None:
    """Keep the last raw pull on disk for debugging/replay without spending API calls."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events), encoding="utf-8")
