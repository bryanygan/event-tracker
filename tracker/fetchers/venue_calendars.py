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
import time
import urllib.robotparser
from datetime import date, datetime, timedelta
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


# ---------- HTML parsers for venues without JSON-LD ----------
# Each returns JSON-LD-shaped dicts (name, startDate, url, offers, performer) so `normalize` handles every
# source the same way. startDate is naive local time (these sites show Philly times).

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _clock(s: str) -> str | None:
    """'7 pm' / '7:30 PM' / '19:30' -> '19:30'."""
    m = re.search(r"(\d{1,2})(?::(\d\d))?\s*([ap])\.?m", s or "", re.I)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
        return f"{h:02d}:{m.group(2) or '00'}"
    m = re.search(r"\b(\d{1,2}):(\d\d)\b", s or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _date_from_weekday(label: str, today: date) -> date | None:
    """'Thu, Oct 22' has no year; the weekday pins it (Oct 22 is a Thursday in 2026, not 2027)."""
    m = re.match(r"\s*(\w{3})\w*,?\s+(\w{3})\w*\.?\s+(\d{1,2})", label or "")
    if not m:
        return None
    for year in (today.year, today.year + 1):
        try:
            d = datetime.strptime(f"{m.group(2)} {m.group(3)} {year}", "%b %d %Y").date()
        except ValueError:
            continue
        if d.strftime("%a").lower() == m.group(1).lower()[:3] and d >= today - timedelta(days=1):
            return d
    return None


_IMG = re.compile(r'<img[^>]+?(?:data-src|src)="(https://[^"]+\.(?:jpe?g|png|webp)[^"]*)"', re.I)


def parse_rhp(page: str, today: date | None = None) -> list[dict]:
    """The 'rhp' WordPress events plugin (Johnny Brenda's, Kung Fu Necktie)."""
    today = today or date.today()
    items = []
    for block in page.split("rhp-event__single-event--list")[1:]:  # list layout only (grid repeats featured shows)
        title = re.search(r'rhp-event__title--list[^>]*>(.*?)</h\d>', block, re.S)
        day = re.search(r'singleEventDate[^>]*>([^<]+)<', block)
        if not title or not day:
            continue
        d = _date_from_weekday(day.group(1), today)
        if not d:
            continue
        times = re.search(r'rhp-event__time-text--list[^>]*>([^<]+)<', block)
        show = times and (re.search(r"show:?\s*([^/]+)", times.group(1), re.I) or re.search(r"(.+)", times.group(1)))
        clock = _clock(show.group(1)) if show else None
        price = re.search(r'rhp-event__cost-text--list[^>]*>\s*\$?([\d.]+)', block)
        link = re.search(r'href="(https://www\.etix\.com/ticket/[^"]+)"', block)
        cta = re.search(r'class="rhp-event-cta ([\w-]+)', block)
        image = _IMG.search(block)  # the show's poster (the venue's own upload)
        name = _text(title.group(1))
        lineup = [p.strip() for p in re.split(r"\s+[~/]\s+", name) if p.strip()]  # "A ~ B ~ C" bills
        items.append({
            "@type": "Event", "name": name,
            "startDate": f"{d.isoformat()}T{clock}:00" if clock else d.isoformat(),
            "url": link and link.group(1),
            "offers": {"url": link and link.group(1), "price": price and price.group(1),
                       "availability": "SoldOut" if cta and "sold" in cta.group(1) else "InStock"},
            "performer": [{"name": p} for p in lineup] if len(lineup) > 1 else "Organization",
            "image": image and html.unescape(image.group(1)),
        })
    return items


def parse_philamoca(page: str, today: date | None = None) -> list[dict]:
    """PhilaMOCA's own markup: <a class="event event--…"> blocks with <time datetime> dates."""
    items = []
    for m in re.finditer(r'<a class="event event--([\w-]+)" href="([^"]+)">(.*?)</a>\s*<!-- end \.event', page, re.S):
        state, url, block = m.groups()
        d = re.search(r'class="event__date" datetime="(\d{4}-\d\d-\d\d)"', block)
        title = re.search(r'class="event__title">(.*?)</span>', block, re.S)
        if not d or not title:
            continue
        label = re.search(r'class="event__label">(.*?)</span>', block, re.S)
        show = re.search(r'Show<span>:</span></span>\s*<time[^>]*datetime="(\d\d:\d\d)"', block) or \
            re.search(r'event__details-value" datetime="(\d\d:\d\d)"', block)
        price = re.search(r'Tickets<span>:</span></span>\s*<span class="event__detail-value">\s*\$?([\d.]+)', block)
        image = _IMG.search(block)
        items.append({
            "@type": "Event",
            "name": _text(title.group(1)),
            "label": _text(label.group(1)) if label else None,  # "Philly Premiere" etc.; checked by the junk filter
            "startDate": f"{d.group(1)}T{show.group(1)}:00" if show else d.group(1),
            "url": url,
            "offers": {"url": url, "price": price and price.group(1),
                       "availability": "SoldOut" if "sold" in state else "InStock"},
            "eventStatus": "EventCancelled" if "cancel" in state else None,
            "performer": "Organization",
            "image": image and html.unescape(image.group(1)),
        })
    return items


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_SPAN = re.compile(r'm-date__(month|day|year)">([^<]*)<')
MAX_RUN_DAYS = 14  # a date range longer than this is a season (a residency, a store's hours), not a run of shows


def _card_dates(block: str) -> list[date]:
    """'Nov. 10, 2026', or a range 'Nov. 7 - 8, 2026' / 'Dec. 30 - Jan. 2, 2027': every day it covers."""
    first = block.split("m-date__rangeLast")[0]
    parts = {"first": _SPAN.findall(first), "last": _SPAN.findall(block[len(first):])}
    try:
        days = []
        for side in ("first", "last"):
            month = next((_MONTHS.get(v.strip().lower()[:3]) for k, v in parts[side] if k == "month"), None)
            day = next((int(v) for k, v in parts[side] if k == "day"), None)
            year = next((int(re.sub(r"\D", "", v)) for k, v in parts[side] if k == "year"), None)
            if day:
                days.append([year, month or days[0][1], day])
        end_year = days[-1][0] or days[0][0]
        start_year = days[0][0] if days[0][0] and len(days) > 1 else end_year - (days[0][1] > days[-1][1])
        end, start = date(end_year, *days[-1][1:]), date(start_year, *days[0][1:])  # "Dec. 30 - Jan. 2, 2027"
    except (TypeError, ValueError, IndexError):
        return []
    if (end - start).days > MAX_RUN_DAYS:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def parse_eventitem(page: str, today: date | None = None) -> list[dict]:
    """Event cards ('eventItem': title, dates, the venue's own image), the layout Xfinity Mobile Arena's site uses.
    A card covering several nights becomes one entry per night, so each Ticketmaster night finds its twin."""
    if page.lstrip().startswith('"'):  # the load-more endpoint answers with the HTML as a JSON string
        page = json.loads(page)
    items = []
    for block in re.split(r'<div class="eventItem\b', page)[1:]:
        title = re.search(r'<h3 class="title[^"]*">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not title:
            continue
        image = _IMG.search(block)
        for d in _card_dates(block):
            items.append({"@type": "Event", "name": _text(title.group(2)), "startDate": d.isoformat(),
                          "url": title.group(1), "image": image and html.unescape(image.group(1))})
    return items


PARSERS = {"jsonld": lambda page, today=None: jsonld_events(page), "rhp": parse_rhp, "philamoca": parse_philamoca,
           "eventitem": parse_eventitem}


def _pages(venue: dict, session: requests.Session) -> list[str]:
    """The venue's page, or for a paged calendar (venue["page_size"]: a "load more" endpoint whose URL takes an
    {offset}) every page until one comes back short, a second apart."""
    if not venue.get("page_size"):
        r = session.get(venue["url"], headers={"User-Agent": UA}, timeout=45)  # PhilaMOCA can be slow
        r.raise_for_status()
        return [r.text]
    out = []
    for i in range(venue.get("max_pages", 40)):
        if i:
            time.sleep(1)
        r = session.get(venue["url"].format(offset=i * venue["page_size"]), timeout=45,
                        headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"})
        r.raise_for_status()
        out.append(r.text)
        if len(re.findall(r'class=\\?"eventItem\b', r.text)) < venue["page_size"]:  # \" inside the JSON string
            break
    return out


def fetch_venue(venue: dict, session: requests.Session | None = None) -> list[dict]:
    """Normalized upcoming shows for one configured venue. Raises SourceError on any failure."""
    session = session or requests.Session()
    if not _robots_allows(venue["url"].replace("{offset}", "0"), session):
        raise SourceError("robots.txt disallows reading this page")
    try:
        pages = _pages(venue, session)
    except requests.RequestException as exc:
        raise SourceError(f"fetch failed: {exc}") from exc
    raw = [item for page in pages for item in PARSERS[venue.get("parser", "jsonld")](page)]
    if not raw:
        raise SourceError("no events found on the page (site changed?)")
    shows = {}
    for item in raw:
        ev = normalize(item, venue)
        if ev and not is_junk(ev["name"]) and not is_junk(item.get("label") or ""):
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
    for site, pattern in (("tixr", r"tixr\.com/.*-(\d+)(?:[/?#]|$)"), ("dice", r"dice\.fm/event/([A-Za-z0-9]+)"),
                          ("etix", r"etix\.com/ticket/p/(\d+)")):
        m = re.search(pattern, url)
        if m:
            return f"cal:{venue_key}:{site}-{m.group(1)}"
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
    kept = []
    for c in calendar_events:
        twin = next((t for t in by_date.get(c["local_date"], []) if same_show(c, t)), None)
        if twin is None:
            kept.append(c)
        elif c.get("image_url"):  # Ticketmaster's copy wins, but the venue's image is usually the Philly poster
            twin["venue_image_url"] = c["image_url"]
    return kept, len(calendar_events) - len(kept)
