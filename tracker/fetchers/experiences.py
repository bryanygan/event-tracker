"""Philly experiences beyond concerts (creator-roadmap P2-14): markets, festivals, seasonal events.

Sources are in config.EXPERIENCE_SOURCES; the survey behind them is docs/agent-notes/experience-sources.md.
Each publishes its own machine-readable listings: Center City District through The Events Calendar's public API,
the Delaware River Waterfront through an iCal file per event. robots.txt is checked before each read.

Output matches `ticketmaster.normalize()` (segment "Experience", the category in `genre`, the last day of a
multi-day event in `end_date`), so the store and detectors treat these like any other event. Prices are kept
only when the listing gives a number, and "free" (price 0) only when the listing itself says so.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests

from .. import config
from .venue_calendars import UA, SourceError, TZ, _robots_allows

log = logging.getLogger(__name__)

_CANCELLED = re.compile(r"^\s*cancell?ed\s*[-–—:]\s*", re.I)
_POSTPONED = re.compile(r"^\s*postponed\s*[-–—:]\s*", re.I)
_TAGS = re.compile(r"^\s*\((?:ticketed event|free event|rsvp required)\)\s*", re.I)  # DRWC title prefixes


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:80]


def clean_title(raw: str) -> tuple[str, str]:
    """'Canceled &#8211; Open Streets: West Walnut' -> ('Open Streets: West Walnut', 'cancelled')."""
    name = re.sub(r"\s+", " ", html.unescape(html.unescape(raw or ""))).strip()
    status = "onsale"
    if _CANCELLED.search(name):
        name, status = _CANCELLED.sub("", name), "cancelled"
    elif _POSTPONED.search(name):
        name, status = _POSTPONED.sub("", name), "postponed"
    return _TAGS.sub("", name).strip(" -–|"), status


def category_for(name: str) -> str:
    for category, pattern in config.EXPERIENCE_CATEGORY_WORDS:
        if re.search(pattern, name, re.I):
            return category
    return "Other"


def _event(source: dict, key: str, name: str, status: str, start: date, end: date | None, time_: str | None,
           url: str | None, venue: str, city: str | None, price: tuple | None = None, image: str | None = None,
           category: str | None = None) -> dict:
    lo, hi = price or (None, None)
    return {
        "id": f"{source['key']}:{key}",
        "name": name,
        "url": url,
        "image_url": image,
        "local_date": start.isoformat(),
        "end_date": end.isoformat() if end and end > start else None,
        "local_time": f"{time_}:00" if time_ else None,
        "status": status,
        "segment": "Experience",
        "genre": category or category_for(name), "subgenre": None,
        "price_min": lo, "price_max": hi, "currency": "USD" if lo is not None else None,
        "onsale_start": None, "onsale_end": None,
        "venue": {"id": f"{source['key']}:{slug(venue)}", "name": venue, "city": city or "Philadelphia",
                  "state": "PA"},
        # The listing itself is the "headliner": scripts and the dashboard name events by it
        "attractions": [{"id": f"exp:{slug(name)}", "name": name, "genre": None, "subgenre": None,
                         "tm_upcoming_total": None}],
        "presales": [],
        "source": source["key"],
    }


# ---------- Center City District (The Events Calendar API) ----------

def tribe_price(e: dict) -> tuple | None:
    """Numbers from cost_details, or 0 when the listing's own cost text says free. Otherwise nothing."""
    values = []
    for v in (e.get("cost_details") or {}).get("values") or []:
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
    if values:
        return min(values), max(values)
    if re.fullmatch(r"\s*free\s*", html.unescape(e.get("cost") or ""), re.I):
        return 0.0, 0.0
    return None


def collapse_tribe(items: list[dict], source: dict, today: date) -> list[dict]:
    """One event per title. Back-to-back dates are one run (a market open daily for six weeks, maybe closed a day
    here and there: "Nov 14 – Jan 1"); a weekly or monthly series isn't, so it shows as its next date only (a
    range would say it's on every day in between). The time and link come from the next upcoming date."""
    groups: dict[str, list[dict]] = {}
    for e in items:
        name, _ = clean_title(e.get("title"))
        if name and not e.get("hide_from_listings"):
            groups.setdefault(name.casefold(), []).append(e)
    out = []
    for occurrences in groups.values():
        occurrences.sort(key=lambda e: e["start_date"])
        runs: list[list[tuple]] = []  # [(occurrence, first day, last day)], consecutive days
        for e in occurrences:
            first = date.fromisoformat(e["start_date"][:10])
            last = max(first, date.fromisoformat((e.get("end_date") or e["start_date"])[:10]))
            if runs and (first - runs[-1][-1][2]).days <= RUN_GAP_DAYS:
                runs[-1].append((e, first, last))
            else:
                runs.append([(e, first, last)])
        run = next((r for r in runs if max(x[2] for x in r) >= today), None)  # on now, or the next one
        if run is None:
            continue
        nxt = next((e for e, _, last in run if last >= today), run[-1][0])
        name, status = clean_title(nxt.get("title"))
        venue = nxt.get("venue") if isinstance(nxt.get("venue"), dict) else {}
        image = nxt.get("image") if isinstance(nxt.get("image"), dict) else {}
        clock = None if nxt.get("all_day") else nxt["start_date"][11:16]
        out.append(_event(source, slug(name), name, status, run[0][1], max(x[2] for x in run), clock, nxt.get("url"),
                          html.unescape(venue.get("venue") or source["name"]), venue.get("city"),
                          tribe_price(nxt), image.get("url")))
    return out


RUN_GAP_DAYS = 2  # a market closed one day still counts as one run; a weekly series is separate dates


def fetch_tribe(source: dict, session: requests.Session, today: date) -> list[dict]:
    api = source["url"].rstrip("/") + "/wp-json/tribe/events/v1/events"
    if not _robots_allows(api, session):
        raise SourceError("robots.txt disallows the events API")
    start = today - timedelta(days=config.EXPERIENCE_LOOKBACK_DAYS)
    end = today + timedelta(days=config.EXPERIENCE_LOOKAHEAD_DAYS)
    items = []
    for page in range(1, config.EXPERIENCE_MAX_PAGES + 1):
        if page > 1:
            time.sleep(config.EXPERIENCE_DELAY_SECONDS)
        try:
            r = session.get(api, headers={"User-Agent": UA}, timeout=30, params={
                "per_page": 50, "page": page, "start_date": start.isoformat(), "end_date": end.isoformat()})
        except requests.RequestException as exc:
            raise SourceError(f"fetch failed: {exc}") from exc
        if r.status_code == 400 and page > 1:  # past the last page
            break
        if r.status_code != 200:
            raise SourceError(f"HTTP {r.status_code}")
        body = r.json()
        items += body.get("events") or []
        if page >= (body.get("total_pages") or 1):
            break
    return collapse_tribe(items, source, today)


# ---------- Delaware River Waterfront (event list + one iCal file per event) ----------

def ics_events(text: str) -> list[dict]:
    """VEVENT blocks as {SUMMARY, DTSTART, DTEND, LOCATION, ...}. Folded lines are unfolded first."""
    text = re.sub(r"\r?\n[ \t]", "", text)
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        fields = {}
        for line in block.splitlines():
            m = re.match(r"([A-Z-]+)(?:;[^:]*)?:(.*)", line)
            if m and m.group(1) not in fields:
                fields[m.group(1)] = m.group(2).strip()
        out.append(fields)
    return out


def ics_time(value: str) -> datetime | date | None:
    """'20261127T160000Z' -> aware local datetime; '20261127' -> date."""
    if not value:
        return None
    try:
        if "T" not in value:
            return datetime.strptime(value[:8], "%Y%m%d").date()
        dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
        return dt.replace(tzinfo=timezone.utc).astimezone(TZ) if value.endswith("Z") else dt.replace(tzinfo=TZ)
    except ValueError:
        return None


def ics_unescape(s: str) -> str:
    return re.sub(r"\\([,;\\])", r"\1", (s or "").replace("\\n", " ").replace("\\N", " ")).strip()


def drwc_event(source: dict, slug_: str, fields: dict) -> dict | None:
    start, end = ics_time(fields.get("DTSTART")), ics_time(fields.get("DTEND"))
    name, status = clean_title(ics_unescape(fields.get("SUMMARY")))
    if not start or not name:
        return None
    start_day = start.date() if isinstance(start, datetime) else start
    clock = start.strftime("%H:%M") if isinstance(start, datetime) else None
    end_day = None
    if end:
        # an event ending in the small hours (1 AM) belongs to the day before
        end_day = (end - timedelta(hours=6)).date() if isinstance(end, datetime) else end - timedelta(days=1)
    location = ics_unescape(fields.get("LOCATION"))
    venue = location.split(",")[0].strip() if location else source["name"]
    return _event(source, slug_, name, status, start_day, end_day, clock,
                  f"{source['url'].rstrip('/')}/what-to-do/{slug_}", venue or source["name"], "Philadelphia")


def fetch_drwc(source: dict, session: requests.Session, today: date) -> list[dict]:
    base = source["url"].rstrip("/")
    listing = base + "/what-to-do"
    if not _robots_allows(listing, session):
        raise SourceError("robots.txt disallows the event list")
    try:
        r = session.get(listing, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SourceError(f"fetch failed: {exc}") from exc
    slugs = list(dict.fromkeys(re.findall(r'href="/what-to-do/([a-z0-9-]+)"', r.text)))[:20]
    if not slugs:
        raise SourceError("no events found on the list page (site changed?)")
    out = []
    for s in slugs:
        time.sleep(config.EXPERIENCE_DELAY_SECONDS)
        url = f"{base}/apos-events/vcal?slug={quote(s)}"
        try:
            ics = session.get(url, headers={"User-Agent": UA}, timeout=30)
        except requests.RequestException as exc:
            log.warning("Waterfront calendar file for %s failed: %s", s, exc)
            continue
        if ics.status_code != 200:
            continue
        for fields in ics_events(ics.content.decode("utf-8", errors="replace")):
            ev = drwc_event(source, s, fields)
            if ev and (ev["end_date"] or ev["local_date"]) >= today.isoformat():
                out.append(ev)
    if not out:
        raise SourceError("no readable calendar files")
    return out


FETCHERS = {"tribe": fetch_tribe, "drwc": fetch_drwc}


def fetch_source(source: dict, session: requests.Session | None = None, today: date | None = None) -> list[dict]:
    """Upcoming listings from one configured source. Raises SourceError on any failure."""
    session = session or requests.Session()
    today = today or datetime.now(TZ).date()
    try:
        return FETCHERS[source["kind"]](source, session, today)
    except SourceError:
        raise
    except (ValueError, KeyError, TypeError) as exc:  # a reply we can't read is a failed source, not a crash
        raise SourceError(f"unreadable reply: {type(exc).__name__}: {exc}") from exc


# ---------- events you add yourself ----------

def manual_event(row) -> dict:
    """A manual_events row as an event. Every fact is yours; the price is only what you typed."""
    src = {"key": "man", "name": "Added by you"}
    lo, hi = row["price_min"], row["price_max"]
    ev = _event(src, str(row["id"]), row["name"], "onsale", date.fromisoformat(row["start_date"]),
                date.fromisoformat(row["end_date"]) if row["end_date"] else None, row["start_time"], row["url"],
                row["venue"], row["city"], (lo, hi if hi is not None else lo) if lo is not None else None,
                category=row["category"] or "Other")
    ev["source"] = "manual"
    return ev
