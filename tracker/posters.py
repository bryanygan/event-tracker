"""Show posters as visuals for drafts and slides.

Where they come from: the image each event already carries from its source, labelled by what it usually is.
- Venue calendars (Tixr, DICE and the venues' own sites): the show's own Philly poster (`poster`). When the
  venue's listing duplicates a Ticketmaster show, its image is kept on the Ticketmaster event (`venue_image_url`).
- Ticketmaster `/dam/e/` images: made for that event (`event_image`); sometimes the poster, sometimes not.
- Ticketmaster `/dam/a/` images: the artist's press photo (`artist_photo`), never called a poster.
- Ticketmaster `/dam/c/` images: a generic category picture (`generic`), ignored.

Posters and event images are downloaded once into data/posters/ (a batch after each run, the rest when you
open an idea) and served from there. A poster is a picture, never a fact: it was made weeks before and can
show an old date, venue or lineup, so `warnings` flags shows whose listing changed. Posters belong to the
venue, promoter or artist: they're credited on slides and left out of the public showcase.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from . import config
from .store import now_iso

log = logging.getLogger(__name__)

POSTER_DIR = config.DATA_DIR / "posters"
USABLE = ("poster", "event_image")  # downloaded and offered for slides
LABELS = {"poster": "Venue image (usually the poster)", "event_image": "Ticketmaster event image",
          "artist_photo": "Artist photo (not a poster)"}
MAX_BYTES = 8_000_000
_TYPES = {b"\x89PNG\r\n\x1a\n": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg")}
_UA = "PhillyEventsTracker/0.1 (personal project; saves each show's poster once)"
_STALE_STATUS = {"cancelled", "canceled", "postponed", "rescheduled"}
_EXPERIENCE = {s["key"] for s in config.EXPERIENCE_SOURCES}


def image_of(e) -> tuple[str | None, str | None]:
    """The event's best image and what it is: the venue's poster for a Ticketmaster show wins over Ticketmaster's."""
    if e["venue_image_url"]:
        return e["venue_image_url"], "poster"
    return e["image_url"], kind_of(e["image_url"], e["source"])


def kind_of(image_url: str | None, source: str | None) -> str | None:
    if not image_url:
        return None
    if source in _EXPERIENCE:
        return "event_image"  # listings' photos, not show posters
    if source:
        return "poster"
    path = urlparse(image_url).path
    if "/dam/e/" in path:
        return "event_image"
    if "/dam/a/" in path:
        return "artist_photo"
    return "generic"


def _sniff(data: bytes) -> tuple[str, str] | None:
    for magic, t in _TYPES.items():
        if data.startswith(magic):
            return t
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _event(conn, event_id: str):
    return conn.execute("""SELECT e.id, e.name, e.image_url, e.venue_image_url, e.source, e.local_date, e.local_time, e.status,
                                  e.venue_id, v.name AS venue_name
                           FROM events e LEFT JOIN venues v ON v.id = e.venue_id WHERE e.id=?""",
                        (event_id,)).fetchone()


def fetch(conn, event_id: str, session=None) -> dict | None:
    """Download this event's poster unless we already have the current one. Returns the poster row."""
    e = _event(conn, event_id)
    url, kind = image_of(e) if e else (None, None)
    if kind not in USABLE:
        return None
    row = conn.execute("SELECT * FROM posters WHERE event_id=?", (event_id,)).fetchone()
    if row and row["image_url"] == url and (row["file"] or row["error"]):
        return dict(row)
    seen = json.dumps({"date": e["local_date"], "time": e["local_time"], "venue": e["venue_name"], "name": e["name"]})
    file = error = ctype = None
    size = 0
    try:
        if urlparse(url).scheme != "https":
            raise ValueError("not an https link")
        r = (session or requests).get(url, headers={"User-Agent": _UA}, timeout=30, stream=True)
        r.raise_for_status()
        data = b""
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                raise ValueError("over 8 MB")
        sniffed = _sniff(data)
        if not sniffed:
            raise ValueError("not a PNG, JPEG or WebP image")
        ext, ctype = sniffed
        POSTER_DIR.mkdir(parents=True, exist_ok=True)
        file = f"{hashlib.sha1(event_id.encode()).hexdigest()[:16]}.{ext}"
        (POSTER_DIR / file).write_bytes(data)
        size = len(data)
    except (requests.RequestException, ValueError, OSError) as exc:
        error = str(exc)[:200]
        log.info("Poster for %s not saved: %s", event_id, error)
    conn.execute("""INSERT INTO posters (event_id, image_url, kind, file, content_type, bytes, fetched_at, error, seen)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_id) DO UPDATE SET image_url=excluded.image_url, kind=excluded.kind,
                      file=excluded.file, content_type=excluded.content_type, bytes=excluded.bytes,
                      fetched_at=excluded.fetched_at, error=excluded.error, seen=excluded.seen""",
                 (event_id, url, kind, file, ctype, size, now_iso(), error, seen))
    conn.commit()
    return dict(conn.execute("SELECT * FROM posters WHERE event_id=?", (event_id,)).fetchone())


def _alert_event_ids(conn, alert) -> list[str]:
    from .opportunities import _match_shows
    facts = json.loads(alert["facts"]) if isinstance(alert["facts"], str) else alert["facts"]
    ids = json.loads(alert["event_ids"]) if alert["event_ids"] else []
    if ids:
        return ids
    from .scoring import upcoming_events
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    return [e["id"] for e in _match_shows(facts, upcoming_events(conn, today))]  # ideas raised before event_ids


def prefetch(conn, limit: int = config.POSTERS_PER_RUN, pause: float = 0.5) -> int:
    """After a run: save posters for the open event ideas' shows, best ideas first. Returns how many were tried."""
    tried = 0
    session = requests.Session()
    alerts = conn.execute(f"""SELECT id, facts, event_ids FROM alerts WHERE status != 'dismissed'
                              AND kind NOT IN ({','.join('?' * len(config.MUSIC_KINDS))}, ?)
                              ORDER BY score DESC, id DESC""", (*config.MUSIC_KINDS, config.VINYL_KIND)).fetchall()
    for a in alerts:
        for event_id in _alert_event_ids(conn, a):
            if tried >= limit:
                return tried
            e = _event(conn, event_id)
            url, kind = image_of(e) if e else (None, None)
            if kind not in USABLE:
                continue
            row = conn.execute("SELECT image_url FROM posters WHERE event_id=?", (event_id,)).fetchone()
            if row and row["image_url"] == url:
                continue
            fetch(conn, event_id, session)
            tried += 1
            time.sleep(pause)
    return tried


def warnings(conn, event, poster: dict | None) -> list[str]:
    """Why the poster may not match the listing. The listing (our data) is what the script and slides say."""
    out = []
    for c in conn.execute("SELECT old_value, new_value FROM changes WHERE event_id=? AND kind='date' ORDER BY id",
                          (event["id"],)):
        out.append(f"Date or time changed ({c['old_value']} → {c['new_value']}): the poster may show the old one")
    if (event["status"] or "").lower() in _STALE_STATUS:
        out.append(f"The show is {event['status']}: the poster doesn't say so")
    if any(w in (event["name"] or "").lower() for w in ("relocated", "moved to", "new venue", "new date")):
        out.append("The listing says the show moved: the poster may name the old venue or date")
    seen = json.loads(poster["seen"]) if poster and poster.get("seen") else None
    if seen and seen.get("venue") and event["venue_name"] and seen["venue"] != event["venue_name"]:
        out.append(f"Venue changed since the poster was saved ({seen['venue']} → {event['venue_name']})")
    if seen and seen.get("name") and seen["name"] != event["name"]:
        out.append("The bill changed since the poster was saved: openers on the poster may be out of date")
    return list(dict.fromkeys(out))


def for_alert(conn, alert_id: int, fetch_missing: int = 8) -> list[dict]:
    """One entry per show in the idea that has an image: what it is, where to get it, and its warnings.
    Downloads up to `fetch_missing` posters not saved yet."""
    alert = conn.execute("SELECT id, facts, event_ids FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not alert:
        raise KeyError(alert_id)
    out = []
    for event_id in _alert_event_ids(conn, alert):
        e = _event(conn, event_id)
        url, kind = image_of(e) if e else (None, None)
        if kind not in LABELS:
            continue
        poster = None
        if kind in USABLE:
            row = conn.execute("SELECT * FROM posters WHERE event_id=?", (event_id,)).fetchone()
            poster = dict(row) if row and row["image_url"] == url else None
            if poster is None and fetch_missing > 0:
                fetch_missing -= 1
                poster = fetch(conn, event_id)
        out.append({
            "event_id": event_id, "name": e["name"], "venue": e["venue_name"], "date": e["local_date"],
            "kind": kind, "label": LABELS[kind], "source_url": url,
            "saved": bool(poster and poster.get("file")), "error": poster and poster.get("error"),
            "credit": e["venue_name"] if kind == "poster" else "Ticketmaster" if kind == "event_image" else None,
            "warnings": warnings(conn, e, poster) if kind in USABLE else [],
        })
    return out


def file_for(conn, event_id: str) -> tuple[str, str] | None:
    """(path, content type) of a saved poster, for the dashboard."""
    row = conn.execute("SELECT file, content_type FROM posters WHERE event_id=?", (event_id,)).fetchone()
    if not row or not row["file"]:
        return None
    path = POSTER_DIR / row["file"]
    return (str(path), row["content_type"]) if path.exists() else None
