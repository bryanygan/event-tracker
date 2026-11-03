"""Show posters from Instagram: the venue's "just announced" post and the artist's own tour poster.

How: Meta's Business Discovery reads other professional accounts' public posts through your own creator account
(config.META_ACCESS_TOKEN). Read only, nothing is posted. The token goes in a header, so it never ends up in a URL,
an exception message or a log.
- Venues (config.INSTAGRAM_VENUES): after each 6:30/16:30 run, every venue's newest posts, and older pages once
  (back INSTAGRAM_HISTORY_DAYS), since big shows are announced months ahead. A post that names an upcoming show at
  that venue has its image saved straight away, because Instagram's image links stop working after a while.
- Artists: their handle comes from their MusicBrainz links (kept INSTAGRAM_HANDLE_DAYS); their posts are read when
  you open an idea's posters.
- Matching is plain text. A venue post must name the artist (whole words); the show's date in the caption makes it
  a stronger match. An artist's post must mention Philly, the venue or the date; otherwise a tour post is offered
  as a general tour poster, labelled so.
- Budget: INSTAGRAM_HOURLY_CAP of our calls an hour, and none while Meta's usage header says 75% or more.
Like the listing images (posters.py), a post is a picture, never a fact: dates and openers come from the listing.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from . import config, db, posters
from .listening import artist_key
from .search import fold
from .store import now_iso

log = logging.getLogger(__name__)

PAGE_SIZE = 50
FIELDS = "id,timestamp,media_type,caption,media_url,thumbnail_url,permalink"
USAGE_STOP = 75  # Meta's x-app-usage percentage at which we stop for the hour
MAX_PER_SHOW = 3
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_PHILLY = (" philly ", " philadelphia ", " phl ", " phila ")
_TOURISH = re.compile(r"\b(tour|tickets|on sale|presale|dates)\b")
_HANDLE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})", re.I)
_NOT_HANDLES = {"p", "reel", "reels", "explore", "stories", "tv", "accounts"}
_STRONG = "names the artist and the date"
_TOUR = "a general tour post"


class Budget(Exception):
    """Out of Instagram calls for this hour (ours or Meta's)."""


class GraphError(Exception):
    pass


def enabled() -> bool:
    return bool(config.META_ACCESS_TOKEN)


def _norm(text: str | None) -> str:
    """Lowercase words like " the fillmore philly ": accents off, & as and, padded so " x " matches whole words."""
    t = unicodedata.normalize("NFKD", (text or "").replace("&", " and ")).casefold()
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " " + re.sub(r"[\W_]+", " ", t).strip() + " "


def _parse_ts(ts: str) -> str:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc).replace(microsecond=0).isoformat()


# ---------- calls ----------

def _hour_key(now: datetime) -> str:
    return "instagram:calls:" + now.strftime("%Y-%m-%dT%H")


def calls_this_hour(conn, now: datetime | None = None) -> int:
    row = conn.execute("SELECT body FROM api_cache WHERE key=?", (_hour_key(now or datetime.now(timezone.utc)),)).fetchone()
    return int(row["body"]) if row else 0


def _meta_busy(conn, now: datetime) -> bool:
    row = conn.execute("SELECT body, fetched_at FROM api_cache WHERE key='instagram:usage'").fetchone()
    return bool(row and int(row["body"]) >= USAGE_STOP
                and datetime.fromisoformat(row["fetched_at"]) > now - timedelta(hours=1))


def _graph(conn, path: str, params: dict, session=None) -> dict:
    """One Graph API GET, counted against the hourly cap. Raises Budget, GraphError or requests.RequestException."""
    now = datetime.now(timezone.utc)
    if calls_this_hour(conn, now) >= config.INSTAGRAM_HOURLY_CAP or _meta_busy(conn, now):
        raise Budget()
    conn.execute("""INSERT INTO api_cache (key, body, fetched_at) VALUES (?, '1', ?)
                    ON CONFLICT(key) DO UPDATE SET body = CAST(body AS INTEGER) + 1""", (_hour_key(now), now_iso()))
    conn.commit()
    r = (session or requests).get(f"{config.GRAPH_URL}/{path}", params=params, timeout=30,
                                  headers={"Authorization": f"Bearer {config.META_ACCESS_TOKEN}"})
    try:
        usage = json.loads(r.headers.get("x-app-usage") or "{}")
        pct = max(int(v) for v in usage.values()) if usage else 0
        conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES ('instagram:usage', ?, ?)",
                     (str(pct), now_iso()))
    except (ValueError, TypeError):
        pass
    try:
        body = r.json()
    except ValueError:
        raise GraphError(f"Instagram answered {r.status_code} without JSON")
    if "error" in body:
        e = body["error"]
        raise GraphError(e.get("error_user_msg") or e.get("message") or f"error {e.get('code')}")
    return body


def user_id(conn, session=None) -> str:
    """Your Instagram account's id, found once through your Facebook Page."""
    row = conn.execute("SELECT body FROM api_cache WHERE key='instagram:user_id'").fetchone()
    if row:
        return row["body"]
    body = _graph(conn, "me/accounts", {"fields": "instagram_business_account"}, session)
    for page in body.get("data") or []:
        if page.get("instagram_business_account"):
            uid = page["instagram_business_account"]["id"]
            conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES ('instagram:user_id', ?, ?)",
                         (uid, now_iso()))
            conn.commit()
            return uid
    raise GraphError("No Instagram account is linked to your Facebook Page")


# ---------- accounts and posts ----------

def _account(conn, handle: str):
    return conn.execute("SELECT * FROM instagram_accounts WHERE handle=?", (handle,)).fetchone()


def stale(conn, handle: str, now: datetime | None = None) -> bool:
    a = _account(conn, handle)
    now = now or datetime.now(timezone.utc)
    return not a or not a["fetched_at"] or \
        datetime.fromisoformat(a["fetched_at"]) < now - timedelta(hours=config.INSTAGRAM_REFRESH_HOURS)


def fetch_account(conn, handle: str, session=None, older: bool = False, history: bool = True) -> int:
    """Read one page of the account's posts: the newest, or (older) the next page back. Returns posts stored.
    A failure (not a professional account, renamed) is kept on the account so it isn't retried until it's stale."""
    a = _account(conn, handle)
    media = f"media.limit({PAGE_SIZE})"
    if older:
        if not (a and a["history_cursor"]):
            return 0
        media += f".after({a['history_cursor']})"
    fields = f"business_discovery.username({handle}){{{media}{{{FIELDS}}}}}"
    try:
        body = _graph(conn, user_id(conn, session), {"fields": fields}, session)
    except GraphError as exc:
        log.info("Instagram @%s: %s", handle, exc)
        conn.execute("""INSERT INTO instagram_accounts (handle, fetched_at, error, history_done) VALUES (?,?,?,1)
                        ON CONFLICT(handle) DO UPDATE SET fetched_at=excluded.fetched_at, error=excluded.error""",
                     (handle, now_iso(), str(exc)[:200]))
        if older:  # don't keep asking for a page that fails
            conn.execute("UPDATE instagram_accounts SET history_cursor=NULL, history_done=1 WHERE handle=?", (handle,))
        conn.commit()
        return 0
    page = (body.get("business_discovery") or {}).get("media") or {}
    posts = page.get("data") or []
    for m in posts:
        conn.execute("""INSERT INTO instagram_posts (media_id, handle, posted_at, media_type, caption, image_url, permalink,
                                                     fetched_at)
                        VALUES (?,?,?,?,?,?,?,?)
                        ON CONFLICT(media_id) DO UPDATE SET caption=excluded.caption, image_url=excluded.image_url,
                          fetched_at=excluded.fetched_at,
                          error=CASE WHEN instagram_posts.file IS NULL THEN NULL ELSE instagram_posts.error END""",
                     (m["id"], handle, _parse_ts(m["timestamp"]), m.get("media_type"), m.get("caption"),
                      m.get("media_url") if m.get("media_type") != "VIDEO" else m.get("thumbnail_url"),
                      m.get("permalink"), now_iso()))
    # Business Discovery gives a cursor but no "next" link: a full page means there may be more
    cursor = ((page.get("paging") or {}).get("cursors") or {}).get("after") if len(posts) >= PAGE_SIZE else None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.INSTAGRAM_HISTORY_DAYS)).isoformat()
    done = int(not history or not cursor or not posts or min(_parse_ts(m["timestamp"]) for m in posts) < cutoff)
    if older:
        conn.execute("UPDATE instagram_accounts SET history_cursor=?, history_done=? WHERE handle=?",
                     (None if done else cursor, done, handle))
    elif a is None or (a["history_cursor"] is None and not a["history_done"]):  # first read: history starts here
        conn.execute("""INSERT INTO instagram_accounts (handle, fetched_at, history_cursor, history_done, error)
                        VALUES (?,?,?,?,NULL)
                        ON CONFLICT(handle) DO UPDATE SET fetched_at=excluded.fetched_at, error=NULL,
                          history_cursor=excluded.history_cursor, history_done=excluded.history_done""",
                     (handle, now_iso(), None if done else cursor, done))
    else:
        conn.execute("UPDATE instagram_accounts SET fetched_at=?, error=NULL WHERE handle=?", (now_iso(), handle))
    conn.commit()
    return len(posts)


def handle_known(conn, name: str) -> bool:
    """Whether the artist's handle (or that they have none) was looked up recently."""
    row = conn.execute("SELECT fetched_at FROM api_cache WHERE key=?", (f"instagram:handle:{fold(name)}",)).fetchone()
    return bool(row and datetime.fromisoformat(row["fetched_at"]) >
                datetime.now(timezone.utc) - timedelta(days=config.INSTAGRAM_HANDLE_DAYS))


def artist_handle(conn, name: str, attraction_id: str | None = None, mb=None) -> str | None:
    """The artist's Instagram from their MusicBrainz links, or None. Kept (found or not) INSTAGRAM_HANDLE_DAYS.
    Raises requests.RequestException when MusicBrainz can't be reached (nothing is cached then)."""
    key = f"instagram:handle:{fold(name)}"
    if handle_known(conn, name):
        return json.loads(conn.execute("SELECT body FROM api_cache WHERE key=?", (key,)).fetchone()["body"])
    known = conn.execute("""SELECT mbid, links FROM artist_identities WHERE (attraction_id=? OR key=?) AND mbid IS NOT NULL
                            ORDER BY links IS NULL LIMIT 1""", (attraction_id, artist_key(name))).fetchone()
    links = json.loads(known["links"]) if known and known["links"] else None
    if not links or not any(l["type"] == "instagram" for l in links):  # old link lists predate reading Instagram
        mbid = known["mbid"] if known else None
        if not mbid and attraction_id:
            a = conn.execute("SELECT setlistfm_mbid FROM attractions WHERE id=?", (attraction_id,)).fetchone()
            mbid = a["setlistfm_mbid"] if a else None
        if mb is None:
            from .fetchers.musicbrainz import MusicBrainzClient
            mb = MusicBrainzClient()
        if not mbid:
            mbid, _ambiguous = mb.find_artist(name)  # ambiguous names get no id, so no handle
        links = mb.links(mbid) if mbid else []
    handle = None
    for l in links:
        m = _HANDLE.search(l["url"]) if l["type"] == "instagram" else None
        if m and m.group(1).lower().rstrip(".") not in _NOT_HANDLES:
            handle = m.group(1).lower().rstrip(".")
            break
    conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES (?,?,?)",
                 (key, json.dumps(handle), now_iso()))
    conn.commit()
    return handle


# ---------- matching ----------

def _date_re(local_date: str) -> re.Pattern:
    d = date.fromisoformat(local_date)
    month = _MONTHS[d.month - 1]
    names = "|".join(dict.fromkeys([month, month[:3], "sept" if d.month == 9 else month[:3]]))
    suffix = r"(?:st|nd|rd|th)?"
    return re.compile(rf"(?<!\d)0?{d.month}\s?[/.]\s?0?{d.day}(?!\d)"
                      rf"|\b(?:{names})\.?\s+0?{d.day}{suffix}\b"
                      rf"|(?<!\d)0?{d.day}{suffix}\s+(?:of\s+)?(?:{names})\b")


def match(post, show: dict) -> str | None:
    """Why this post is this show's poster, or None. `show` comes from _shows (plus its artist_handles)."""
    posted = post["posted_at"][:10]
    earliest = (date.fromisoformat(show["date"]) - timedelta(days=config.INSTAGRAM_HISTORY_DAYS)).isoformat()
    if not (earliest <= posted <= show["date"]):
        return None
    caption = post["caption"] or ""
    words, raw = _norm(caption), caption.casefold()
    dated = bool(_date_re(show["date"]).search(raw))
    if not dated and show["repeats"]:  # another night of the same act there: only a post naming this date
        return None
    if post["handle"] == show["venue_handle"]:
        for name in show["artists"]:
            n = _norm(name)
            if len(fold(name)) >= 3 and n in words and (dated or len(fold(name)) >= 5):
                return _STRONG if dated else "names the artist"
        for h in show["artist_handles"]:
            if f"@{h}" in raw or _norm(h) in words:
                return _STRONG if dated else "tags the artist"
        return None
    if post["handle"] in show["artist_handles"]:
        venue_named = show["venue"] and _norm(show["venue"]) in words
        if dated or venue_named or any(p in words for p in _PHILLY) or \
                (show["venue_handle"] and f"@{show['venue_handle']}" in raw):
            return "the artist's post about Philly" + (" on this date" if dated else "")
        if _TOURISH.search(raw):
            return _TOUR
    return None


def _shows(conn, event_ids: list[str]) -> list[dict]:
    out = []
    for event_id in event_ids:
        e = posters._event(conn, event_id)
        if not e:
            continue
        acts = conn.execute("""SELECT a.id, a.name FROM event_attractions ea JOIN attractions a ON a.id = ea.attraction_id
                               WHERE ea.event_id=? ORDER BY ea.position""", (event_id,)).fetchall()
        out.append({"id": event_id, "event": e, "name": e["name"], "date": e["local_date"], "venue": e["venue_name"],
                    "venue_handle": config.INSTAGRAM_VENUES.get(e["venue_name"] or ""),
                    "artists": [a["name"] for a in acts] or [e["name"]],
                    "attraction_ids": [a["id"] for a in acts] or [None], "artist_handles": [],
                    "repeats": bool(conn.execute(  # the same act at the same venue on another day (2 nights, weekly)
                        """SELECT 1 FROM events o WHERE o.active = 1 AND o.id != ? AND o.venue_id = ?
                           AND o.local_date != ? AND (o.name = ? OR EXISTS (SELECT 1 FROM event_attractions oa
                               WHERE oa.event_id = o.id AND oa.position = 0 AND oa.attraction_id = ?)) LIMIT 1""",
                        (event_id, e["venue_id"], e["local_date"], e["name"], acts[0]["id"] if acts else None)
                    ).fetchone())})
    return out


def _posts_for(conn, show: dict) -> list:
    handles = [h for h in [show["venue_handle"], *show["artist_handles"]] if h]
    if not handles:
        return []
    earliest = (date.fromisoformat(show["date"]) - timedelta(days=config.INSTAGRAM_HISTORY_DAYS)).isoformat()
    return conn.execute(f"""SELECT * FROM instagram_posts WHERE handle IN ({','.join('?' * len(handles))})
                            AND posted_at >= ? AND substr(posted_at, 1, 10) <= ? ORDER BY posted_at DESC""",
                        (*handles, earliest, show["date"])).fetchall()


def _rank(why: str) -> int:
    return 0 if why == _STRONG else 3 if why == _TOUR else 1 if why.startswith(("names", "tags")) else 2


def matches(conn, show: dict) -> list[tuple]:
    """(post, why) for this show, best first: the venue's dated post, the venue's, the artist's Philly post, then
    at most one general tour post."""
    found = [(p, why) for p in _posts_for(conn, show) if (why := match(p, show))]
    found.sort(key=lambda pw: (_rank(pw[1]), -datetime.fromisoformat(pw[0]["posted_at"]).timestamp()))
    tours = [pw for pw in found if pw[1] == _TOUR][:1]
    return ([pw for pw in found if pw[1] != _TOUR] + tours)[:MAX_PER_SHOW]


def save(conn, post, session=None) -> dict:
    """Download a matched post's image once. An expired link is recorded; the post's link still works."""
    if post["file"] or post["error"] or not post["image_url"]:
        return dict(post)
    try:
        file, ctype, _size = posters.save_image(post["image_url"], f"ig_{post['media_id']}", session)
        conn.execute("UPDATE instagram_posts SET file=?, content_type=?, error=NULL WHERE media_id=?",
                     (file, ctype, post["media_id"]))
    except (requests.RequestException, ValueError, OSError) as exc:
        msg = "image link expired: open the post" if isinstance(exc, requests.HTTPError) else str(exc)[:200]
        conn.execute("UPDATE instagram_posts SET error=? WHERE media_id=?", (msg, post["media_id"]))
    conn.commit()
    return dict(conn.execute("SELECT * FROM instagram_posts WHERE media_id=?", (post["media_id"],)).fetchone())


def _entry(conn, show: dict, post: dict, why: str) -> dict:
    warnings = posters.warnings(conn, show["event"], None)
    if why == _TOUR:
        warnings.append("A general tour post: it may not list the Philly date")
    elif "date" not in why:
        warnings.append("The post doesn't mention this date: check it's this show")
    if post["media_type"] == "CAROUSEL_ALBUM":
        warnings.append("A carousel: this is its first image; the show's own slide may be further in (open the post)")
    return {"key": f"ig:{post['media_id']}", "event_id": show["id"], "name": show["name"], "venue": show["venue"],
            "date": show["date"], "kind": "instagram",
            "label": f"@{post['handle']} on Instagram" + (" (tour post)" if why == _TOUR else ""),
            "source_url": post["permalink"], "saved": bool(post["file"]), "error": post["error"],
            "credit": f"@{post['handle']}", "warnings": warnings, "match": why, "posted_at": post["posted_at"],
            "caption": (post["caption"] or "")[:300],
            "image": f"/api/instagram/{post['media_id']}" if post["file"] else None,
            "download": f"/api/instagram/{post['media_id']}?download=1&event_id={quote(show['id'], safe='')}" if post["file"] else None}


def for_shows(conn, event_ids: list[str], session=None, mb=None) -> dict[str, list[dict]]:
    """Instagram posters for these shows (posters.for_alert), looking up at most INSTAGRAM_PER_OPEN accounts."""
    if not enabled() or not event_ids:
        return {}
    shows = _shows(conn, event_ids)
    budget = config.INSTAGRAM_PER_OPEN
    try:
        for h in dict.fromkeys(s["venue_handle"] for s in shows if s["venue_handle"]):
            if budget > 0 and stale(conn, h):
                fetch_account(conn, h, session)
                budget -= 1
        lookups = config.INSTAGRAM_PER_OPEN  # MusicBrainz handle lookups (~2 s each), separate from Meta's
        for s in shows:
            name, attraction_id = s["artists"][0], s["attraction_ids"][0]  # the headliner
            if not handle_known(conn, name):
                if lookups <= 0:
                    continue
                lookups -= 1
            try:
                h = artist_handle(conn, name, attraction_id, mb)
            except requests.RequestException as exc:
                log.info("MusicBrainz lookup for %s failed: %s", name, exc)
                continue
            if h and h != s["venue_handle"]:
                s["artist_handles"].append(h)
                if stale(conn, h):
                    if budget <= 0:
                        continue
                    fetch_account(conn, h, session, history=False)
                    budget -= 1
    except Budget:
        log.info("Instagram: hourly budget used up; showing what's saved")
    except (GraphError, requests.RequestException) as exc:
        log.warning("Instagram lookup failed: %s", type(exc).__name__)
    out = {}
    for s in shows:
        out[s["id"]] = [_entry(conn, s, save(conn, p, session), why) for p, why in matches(conn, s)]
    return out


def refresh(conn, session=None, limit: int = config.INSTAGRAM_PER_RUN) -> dict:
    """After a run: every venue's newest posts, then older pages for history, then save the images of posts that
    name an upcoming show at that venue. Never raises."""
    if not enabled():
        return {"skipped": "no META_ACCESS_TOKEN"}
    handles = sorted(set(config.INSTAGRAM_VENUES.values()))
    calls = 0
    try:
        for h in handles:
            if calls < limit and stale(conn, h):
                fetch_account(conn, h, session)
                calls += 1
        while calls < limit:
            pending = [r["handle"] for r in conn.execute(
                f"""SELECT handle FROM instagram_accounts WHERE history_done=0 AND history_cursor IS NOT NULL
                    AND handle IN ({','.join('?' * len(handles))}) ORDER BY handle""", handles)]
            if not pending:
                break
            for h in pending[:limit - calls]:
                fetch_account(conn, h, session, older=True)
                calls += 1
    except Budget:
        log.info("Instagram: hourly budget reached after %d calls", calls)
    except (GraphError, requests.RequestException) as exc:  # e.g. the token was revoked
        log.warning("Instagram refresh stopped: %s", exc if isinstance(exc, GraphError) else type(exc).__name__)
    return {"calls": calls, "posters_saved": save_venue_matches(conn, session)}


def save_venue_matches(conn, session=None, limit: int = 150) -> int:
    """Save the images of venue posts that name an upcoming show there, while their links still work."""
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    venues = list(config.INSTAGRAM_VENUES)
    ids = [r["id"] for r in conn.execute(
        f"""SELECT e.id FROM events e JOIN venues v ON v.id = e.venue_id WHERE e.active = 1 AND e.local_date >= ?
            AND v.name IN ({','.join('?' * len(venues))}) ORDER BY e.local_date""", (today, *venues))]
    saved = 0
    for s in _shows(conn, ids):
        for p, _why in matches(conn, s):
            if saved >= limit:
                return saved
            if not p["file"] and not p["error"]:
                saved += bool(save(conn, p, session)["file"])
    return saved


def file_for(conn, media_id: str) -> tuple[str, str, str, str] | None:
    """(path, content type, handle, posted day) of a saved Instagram poster."""
    row = conn.execute("SELECT file, content_type, handle, posted_at FROM instagram_posts WHERE media_id=?",
                       (media_id,)).fetchone()
    if not row or not row["file"]:
        return None
    path = posters.POSTER_DIR / row["file"]
    return (str(path), row["content_type"], row["handle"], row["posted_at"][:10]) if path.exists() else None


def main(argv=None):
    from .run import setup_logging
    p = argparse.ArgumentParser(description="Read the venues' Instagram posts now (same as after a run)")
    p.add_argument("--limit", type=int, default=config.INSTAGRAM_PER_RUN)
    args = p.parse_args(argv)
    setup_logging()
    with closing(db.connect()) as conn:
        print(refresh(conn, limit=args.limit))


if __name__ == "__main__":
    main()
