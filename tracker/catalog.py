"""Release catalogs for the music watchlist (docs/agent-notes/music-roadmap.md M4): Deezer and iTunes, the two kept
after the 2026-10-23 bake-off (decisions.md). Deezer lists releases on their day; iTunes also lists pre-orders with
their date. MusicBrainz is only used for artist ids (slow to add new releases, and rate-limited).

Each news check polls CATALOG_ARTISTS_PER_CHECK artists, least recently checked first (artists with a release story
in the last two weeks jump the queue), so every watchlist artist is checked about every 10 hours. Records go into
`releases`, one row per provider; tracker/stories.py turns them into evidence and catalog-only stories.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

import requests

from . import config

log = logging.getLogger(__name__)

KEEP_DAYS = 90          # releases older than this aren't stored (catalogs are refetched every few hours anyway)
ITUNES_RETRY_DAYS = 30  # an artist with no iTunes match is searched again after this


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


FEAT = re.compile(r"\s*[\(\[](?:feat\.?|ft\.?|featuring|with)\s+([^)\]]*)[\)\]]|\s+(?:feat\.?|ft\.?|featuring)\s+(.*)$",
                  re.I)


def featured(title: str, artist: str) -> bool:
    """The artist is a guest on someone else's track: "Static (feat. Kilo Moon)" in Kilo Moon's catalog."""
    target = artist.casefold()
    return any(target in (m.group(1) or m.group(2) or "").casefold() for m in FEAT.finditer(title or ""))


def title_key(title: str | None) -> str:
    """"Glass Hours - Single", "Glass Hours (Deluxe Edition)", "GLASS HOURS" → "glass hours"."""
    t = title or ""
    t = re.sub(r"\s+-\s+(single|ep)\s*$", "", t, flags=re.I)
    t = FEAT.sub("", t)  # "Static (feat. Kilo Moon)" is "Static"
    t = re.sub(r"\s*[\(\[][^)\]]*\b(single|ep|deluxe|edition|version|expanded|remaster\w*|bonus tracks?)\b[^)\]]*[\)\]]",
               "", t, flags=re.I)
    return re.sub(r"\W+", " ", t.casefold().replace("’", "'")).strip()


def due_artists(conn, limit: int) -> list:
    since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    return conn.execute(
        """SELECT w.* FROM artist_identities w
           WHERE w.muted = 0 AND w.tier IN ('core', 'adjacent', 'watch', 'breakout')
           ORDER BY EXISTS (SELECT 1 FROM stories s WHERE s.primary_artist_id = w.id AND s.origin = 'news'
                                AND s.release_status IS NOT NULL AND s.last_seen >= ?) DESC,
                    w.releases_checked_at IS NOT NULL, w.releases_checked_at, w.id
           LIMIT ?""", (since, limit)).fetchall()


def store(conn, artist_id: int, provider: str, releases: list[dict], today: date) -> int:
    """Upsert recent and upcoming releases. Returns how many were new."""
    oldest = (today - timedelta(days=KEEP_DAYS)).isoformat()
    now, new = _now(), 0
    for r in releases:
        if r["release_date"] < oldest or not r["title"]:
            continue
        new += conn.execute("SELECT 1 FROM releases WHERE artist_id=? AND provider=? AND provider_id=?",
                            (artist_id, provider, r["provider_id"])).fetchone() is None
        conn.execute(
            """INSERT INTO releases (artist_id, provider, provider_id, title, title_key, record_type, release_date, url,
                                     first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(artist_id, provider, provider_id) DO UPDATE SET title=excluded.title, title_key=excluded.title_key,
                   record_type=excluded.record_type, release_date=excluded.release_date, url=excluded.url,
                   last_seen=excluded.last_seen""",
            (artist_id, provider, r["provider_id"], r["title"], title_key(r["title"]), r["record_type"],
             r["release_date"], r.get("url"), now, now))
    return new


def poll(conn, limit: int | None = None, deezer_client=None, itunes_client=None, today: date | None = None) -> dict:
    """Check `limit` artists' catalogs. Stops quietly on a rate limit or network error; the rest wait."""
    from .fetchers import deezer, itunes
    limit = config.CATALOG_ARTISTS_PER_CHECK if limit is None else limit
    if limit <= 0:
        return {"artists": 0, "new": 0}
    deezer_client = deezer_client or deezer.DeezerClient()
    itunes_client = itunes_client or itunes.ItunesClient()
    today = today or datetime.now(timezone.utc).date()
    checked = new = 0
    retry = (datetime.now(timezone.utc) - timedelta(days=ITUNES_RETRY_DAYS)).isoformat()
    for a in due_artists(conn, limit):
        try:
            deezer_id = a["deezer_id"]
            if deezer_id is None and a["ids_checked_at"] is None:  # not resolved yet (watchlist.resolve_ids)
                found = deezer_client.find_artist(a["name"])
                deezer_id = found["id"] if found else None
                conn.execute("UPDATE artist_identities SET deezer_id=? WHERE id=?", (deezer_id, a["id"]))
            itunes_id = a["itunes_id"]
            if itunes_id is None and (a["releases_checked_at"] is None or a["releases_checked_at"] < retry):
                itunes_id = itunes_client.find_artist(a["name"])
                conn.execute("UPDATE artist_identities SET itunes_id=? WHERE id=?", (itunes_id, a["id"]))
            if deezer_id:
                new += store(conn, a["id"], "deezer", deezer_client.releases(deezer_id), today)
            if itunes_id:
                new += store(conn, a["id"], "itunes", itunes_client.releases(itunes_id), today)
        except (requests.RequestException, ValueError) as exc:
            log.warning("Catalog checks stopped after %d artists: %s", checked, exc)
            conn.commit()
            break
        conn.execute("UPDATE artist_identities SET releases_checked_at=? WHERE id=?", (_now(), a["id"]))
        conn.commit()
        checked += 1
    oldest = (today - timedelta(days=KEEP_DAYS)).isoformat()
    conn.execute("DELETE FROM releases WHERE release_date < ?", (oldest,))
    conn.commit()
    return {"artists": checked, "new": new}
