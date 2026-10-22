"""Shows from venues' own calendar pages, for rooms that don't sell through Ticketmaster.

Why venue pages: AXS, DICE and Tixr have no public APIs (checked 2026-10-21), but these venues publish
schema.org Event JSON-LD on their own sites (Tixr and DICE generate it), which is the venue choosing
to publish machine-readable listings. One request per venue per run; robots.txt is checked first.
See docs/agent-notes/venue-coverage.md for the survey behind the venue list.

Output matches `ticketmaster.normalize()` so the store, scoring and detectors don't care where a
show came from. These listings have no presale windows and no genre; prices are often present.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import urllib.robotparser
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from .. import config

log = logging.getLogger(__name__)

UA = "PhillyEventsTracker/0.1 (personal project; reads public venue calendars once or twice a day)"
TZ = ZoneInfo(config.TIMEZONE)

# Status written into the show name, e.g. "MARIS ** SOLD OUT **", "Sophie Truax * Canceled *".
_STATUS_MARKERS = [
    (re.compile(r"\*+\s*sold[\s-]*out\s*\*+|\(sold[\s-]*out\)", re.I), "soldout"),
    (re.compile(r"\*+\s*cancell?ed\s*\*+|\(cancell?ed\)", re.I), "cancelled"),
    (re.compile(r"\*+\s*postponed\s*\*+|\(postponed\)", re.I), "postponed"),
    (re.compile(r"\*+\s*(?:new date|rescheduled)[^*]*\*+", re.I), "rescheduled"),
]
_SCHEMA_STATUS = {"EventCancelled": "cancelled", "EventPostponed": "postponed", "EventRescheduled": "rescheduled"}
_JUNK = [re.compile(p, re.I) for p in config.VENUE_JUNK_PATTERNS]


class SourceError(RuntimeError):
    pass


# ---------- fetching ----------

def _robots_allows(url: str, session: requests.Session) -> bool:
    p = urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = session.get(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": UA}, timeout=15)
    except requests.RequestException:
        return True  # unreachable robots.txt: the page fetch will fail on its own if the site is down
    if r.status_code != 200:
        return True  # no robots.txt = no restrictions
    rp.parse(r.text.splitlines())
    return rp.can_fetch(UA, url)


def jsonld_events(page: str) -> list[dict]:
    """Every schema.org *Event object in the page's JSON-LD blocks (handles lists and @graph)."""
    found = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                t = x.get("@type")
                types = t if isinstance(t, list) else [t]
                if any(isinstance(tt, str) and tt.endswith("Event") for tt in types):
                    found.append(x)
                else:
                    stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
    return found


def fetch_venue(venue: dict, session: requests.Session | None = None) -> list[dict]:
    """Normalized upcoming shows for one configured venue. Raises SourceError on any failure."""
    session = session or requests.Session()
    if not _robots_allows(venue["url"], session):
        raise SourceError("robots.txt disallows reading this page")
    try:
        r = session.get(venue["url"], headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SourceError(f"fetch failed: {exc}") from exc
    raw = jsonld_events(r.text)
    if not raw:
        raise SourceError("no schema.org events found on the page (site changed?)")
    shows = {}
    for item in raw:
        ev = normalize(item, venue)
        if ev and not is_junk(ev["name"]):
            shows[ev["id"]] = ev
    return list(shows.values())


# ---------- normalizing ----------

def clean_name(name: str) -> tuple[str, str | None]:
    """'MARIS ** SOLD OUT **' -> ('MARIS', 'soldout'). Also unescapes &amp; and tidies spaces."""
    name = html.unescape(html.unescape(name or ""))
    status = None
    for pattern, value in _STATUS_MARKERS:
        if pattern.search(name):
            status = status or value
            name = pattern.sub("", name)
    name = re.sub(r"\s+", " ", name.replace("\xa0", " ")).strip(" -–:|")
    return name, status


def headliner_and_support(item: dict, name: str) -> tuple[str, list[str]]:
    """Performer names if the venue lists them (DICE, WOW); Tixr venues only say "Organization",
    so fall back to the show name, split on 'w/' / 'with' for support acts."""
    performers = item.get("performer")
    performers = performers if isinstance(performers, list) else [performers]
    names = [html.unescape(p["name"]).strip() for p in performers if isinstance(p, dict) and p.get("name")]
    if names:
        return names[0], names[1:]
    parts = re.split(r"\s+(?:w/|with special guests?|with|featuring|feat\.)\s+", name, maxsplit=1, flags=re.I)
    # commas only: "&"/"and" are part of many band names (Balance & Composure, Florence and the Machine)
    support = [s.strip() for s in parts[1].split(",")] if len(parts) > 1 else []
    # "Dead Wrong: 2nd Annual Holiday Show" / "Band - Tour Name": the artist is the part before the subtitle,
    # which is what Deezer and setlist.fm can match. The full name stays as the show name.
    head = re.split(r":\s+|\s+[-–|]\s+", parts[0], maxsplit=1)[0].strip()
    return (head if len(head) >= 3 else parts[0].strip()), [s for s in support if s]


def _stable_id(venue_key: str, item: dict, local_date: str, name: str) -> str:
    """Prefer the ticket site's own event id (survives renames like '** SOLD OUT **' being added)."""
    offers = item.get("offers")
    offers = offers[0] if isinstance(offers, list) and offers else offers
    url = item.get("url") or (offers or {}).get("url") or ""
    m = re.search(r"tixr\.com/.*-(\d+)(?:[/?#]|$)", url) or re.search(r"dice\.fm/event/([A-Za-z0-9]+)", url)
    if m:
        return f"cal:{venue_key}:{'tixr' if 'tixr' in url else 'dice'}-{m.group(1)}"
    digest = hashlib.sha1(f"{local_date}|{name.casefold()}".encode()).hexdigest()[:10]
    return f"cal:{venue_key}:{digest}"


def _price(offers) -> tuple[float | None, float | None]:
    offers = offers if isinstance(offers, list) else [offers]
    vals = []
    for o in offers:
        if not isinstance(o, dict):
            continue
        for k in ("price", "lowPrice", "highPrice"):
            try:
                v = float(o.get(k))
            except (TypeError, ValueError):
                continue
            if v > 0:
                vals.append(v)
    return (min(vals), max(vals)) if vals else (None, None)


def normalize(item: dict, venue: dict) -> dict | None:
    start = item.get("startDate")
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo:  # listings give UTC: 00:30Z on Jan 8 is 7:30 PM Jan 7 in Philly
        dt = dt.astimezone(TZ)
    name, status = clean_name(item.get("name"))
    if not name:
        return None
    offers = item.get("offers")
    first_offer = (offers[0] if isinstance(offers, list) and offers else offers) or {}
    if "SoldOut" in str(first_offer.get("availability", "")):
        status = status or "soldout"
    status = status or _SCHEMA_STATUS.get(str(item.get("eventStatus", "")).rsplit("/", 1)[-1]) or "onsale"
    headliner, support = headliner_and_support(item, name)
    lo, hi = _price(offers)
    image = item.get("image")
    image = image[0] if isinstance(image, list) and image else image
    local_date = dt.date().isoformat()
    has_time = isinstance(start, str) and "T" in start

    def attraction(n):
        return {"id": "name:" + re.sub(r"\W+", "-", n.casefold()).strip("-"), "name": n, "genre": None,
                "subgenre": None, "tm_upcoming_total": None}

    return {
        "id": _stable_id(venue["key"], item, local_date, name),
        "name": name,
        "url": item.get("url") or first_offer.get("url"),
        "image_url": image if isinstance(image, str) else None,
        "local_date": local_date,
        "local_time": dt.strftime("%H:%M:00") if has_time else None,
        "status": status,
        "segment": "Music",
        "genre": None, "subgenre": None,
        "price_min": lo, "price_max": hi, "currency": "USD" if lo else None,
        "onsale_start": None, "onsale_end": None,
        "venue": {"id": venue.get("venue_id") or f"cal:{venue['key']}", "name": venue["name"],
                  "city": venue.get("city"), "state": venue.get("state", "PA")},
        "attractions": [attraction(n) for n in [headliner, *support]],
        "presales": [],
        "source": venue["key"],
    }


def is_junk(name: str) -> bool:
    return any(p.search(name) for p in _JUNK)


# ---------- dedupe against Ticketmaster ----------

def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").casefold()) if len(w) >= 3 and w not in {"the", "and", "with", "tour"}}


def same_show(cal: dict, tm: dict) -> bool:
    """Same date, same venue, and names overlap. Ticketmaster's copy wins (it has presales and genres)."""
    if cal["local_date"] != tm["local_date"] or not tm.get("venue"):
        return False
    if _words(cal["venue"]["name"]).isdisjoint(_words(tm["venue"]["name"])):
        return False
    a = _words(cal["name"]) | _words(cal["attractions"][0]["name"] if cal["attractions"] else "")
    b = _words(tm["name"]) | {w for x in tm["attractions"] for w in _words(x["name"])}
    return bool(a & b)


def drop_duplicates(calendar_events: list[dict], tm_events: list[dict]) -> tuple[list[dict], int]:
    by_date: dict[str, list[dict]] = {}
    for t in tm_events:
        by_date.setdefault(t["local_date"], []).append(t)
    kept = [c for c in calendar_events if not any(same_show(c, t) for t in by_date.get(c["local_date"], []))]
    return kept, len(calendar_events) - len(kept)
