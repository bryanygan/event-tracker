"""Vinyl videos from your record collection on Discogs: the third pillar, next to events and music news.

sync() reads the collection (fetchers/discogs.py) into vinyl_records, then each record's details (who owns and
wants it, rating, pressing notes, credits), Discogs' suggested prices, and the album's first release date from
MusicBrainz. Lookups are spread over runs (VINYL_MAX_LOOKUPS_PER_RUN) and refreshed on a schedule, so a normal run
makes a handful of calls.

ideas() turns that data into video ideas (alerts of kind "vinyl"). Every number in them is Discogs' or
MusicBrainz's. Lists (top 5 unique pressings, most valuable, most wanted...) are built straight from the data;
story ideas (the story behind a record, an underappreciated album, an anniversary) name a record and are
researched when you ask for a draft (vinyl_research.py). Ideas are never drafted automatically: a story draft
costs a few cents, so it only happens when you click.

An idea is raised once per period (a month for lists, a week for stories, a day for "underrated of the day")
and isn't refreshed afterwards, so prices moving around don't churn your drafts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, listening
from .detectors import fmt_date, save_alerts
from .search import fold
from .store import now_iso

log = logging.getLogger(__name__)
TZ = ZoneInfo(config.TIMEZONE)
KIND = config.VINYL_KIND

FORMAT_LABEL = {
    "vinyl_unique": "Most unique pressings",
    "vinyl_value": "Most valuable records",
    "vinyl_coveted": "Most wanted records",
    "vinyl_variants": "Same album, different pressings",
    "vinyl_haul": "New pickups",
    "vinyl_numbers": "Collection by the numbers",
    "vinyl_look_into": "Records worth looking into",
    "vinyl_underrated": "Underrated vinyl of the day",
    "vinyl_underappreciated": "Underappreciated album",
    "vinyl_lore": "The story behind the record",
    "vinyl_anniversary": "Album anniversary",
}
LIST_FORMATS = ("vinyl_unique", "vinyl_value", "vinyl_coveted", "vinyl_variants", "vinyl_haul", "vinyl_numbers",
                "vinyl_look_into")
STORY_FORMATS = ("vinyl_underrated", "vinyl_underappreciated", "vinyl_lore", "vinyl_anniversary")
MANUAL_FORMATS = ("vinyl_lore", "vinyl_underappreciated", "vinyl_underrated")  # "make an idea" on one record
MILESTONES = (1, 5, 10, 15, 20, 25, 30, 40, 50, 60)
ANNIVERSARY_LOOKAHEAD_DAYS = 10
NM, VGP = "Near Mint (NM or M-)", "Very Good Plus (VG+)"


# ---------- reading Discogs' data ----------

def clean_artist(name: str) -> str:
    """Discogs numbers artists who share a name: "Nova Vale (2)" -> "Nova Vale"."""
    return re.sub(r"\s*\(\d+\)$", "", name or "").strip()


def main_artist(row) -> str:
    """The first credited artist (the credit line joins several with commas, and some names have commas)."""
    names = json.loads(row["artists"] or "[]") if "artists" in row.keys() else []
    return names[0] if names else row["artist"]


def artist_credit(artists: list[dict]) -> str:
    out = ""
    for i, a in enumerate(artists):
        out += clean_artist(a.get("anv") or a["name"])
        if i < len(artists) - 1:
            join = (a.get("join") or ",").strip()
            out += ", " if join == "," else f" {join} "
    return out


_EDITION = re.compile(r"\s*(?:[-–(\[]+\s*(?:\d+\w*\s+(?:year\s+)?anniversary|deluxe|expanded|complete|remaster|"
                      r"special|limited|collector)[^)\]]*[)\]]?|\d+\w*\s+(?:year\s+)?anniversary(?:\s+edition)?|"
                      r"[-–])\s*$", re.I)


def album_title(title: str) -> str:
    """The album's own title without an edition tag, for MusicBrainz: "Shelter (10th Anniversary Edition)" ->
    "Shelter"."""
    prev = None
    while prev != title:
        prev, title = title, _EDITION.sub("", title).strip()
    return title


def pressing(formats: list[dict]) -> str:
    """Discogs' format list as one readable line: "LP, Album, Limited Edition, Translucent Red"."""
    parts = []
    for f in formats or []:
        qty = f.get("qty")
        bits = ([f"{qty}x"] if qty and str(qty) not in ("1", "") else []) + list(f.get("descriptions") or [])
        if f.get("text"):
            bits.append(f["text"])
        parts.append(", ".join(b for b in bits if b and b != "Vinyl"))
    return "; ".join(p for p in parts if p)


def _row_formats(row) -> list[dict]:
    return json.loads(row["formats"] or "[]")


_COLOR_WORDS = re.compile(r"\b(?:red|orange|yellow|green|blue|purple|violet|lavender|pink|magenta|white|grey|gray|"
                          r"silver|gold|brown|tan|cream|bone|beige|teal|turquoise|aqua|cyan|mint|seafoam|sea foam|"
                          r"lilac|maroon|crimson|coke bottle|clear|translucent|transparent|smoke|splatter|marble|"
                          r"glitter|swirl|galaxy|haze|metallic|glow in the dark|pearl|opaque|colou?red)\b", re.I)


def is_colored(formats: list[dict]) -> bool:
    """A color in the format's free text ("Translucent Red", "Coke Bottle Clear"), not just any note."""
    return any(_COLOR_WORDS.search(f.get("text") or "") for f in formats or [])
FEATURES = [  # (pattern on the pressing line + notes, how it reads, points)
    (re.compile(r"test pressing", re.I), "test pressing", 30),
    (re.compile(r"\bsigned\b|autograph", re.I), "signed", 25),
    (re.compile(r"\bnumbered\b", re.I), "numbered", 20),
    (re.compile(r"record store day|\bRSD\b", re.I), "Record Store Day release", 15),
    (re.compile(r"\bshape\b|picture disc", re.I), "shaped or picture disc", 15),
    (re.compile(r"limited edition", re.I), "limited edition", 10),
    (re.compile(r"alternat\w* (?:cover|artwork)|variant cover", re.I), "alternate cover", 10),
    (re.compile(r"zine|burlap|poster|obi|slipmat|box set|o-card", re.I), "extras in the package", 5),
]
_LIMIT = re.compile(r"(?:limited to|edition of|pressing of|run of|only)\s*([\d,]{2,7})\s*(?:copies|units|pieces)?",
                    re.I)


def limited_to(notes: str | None, line: str = "") -> int | None:
    """A stated pressing size ("Limited to 500 copies"), from the release notes or the format text."""
    for text in (notes or "", line):
        m = _LIMIT.search(text)
        if m:
            n = int(m.group(1).replace(",", ""))
            if 10 <= n <= 100_000:
                return n
    return None


def uniqueness(row) -> tuple[int, list[str]]:
    """How unusual this pressing is, 0-100ish, with the reasons. From the pressing line, the release notes (labeled
    as such) and how few Discogs users own it."""
    line = pressing(_row_formats(row))
    points, why = 0, []
    for pattern, label, pts in FEATURES:
        if pattern.search(line):
            points += pts
            why.append(label)
        elif pattern.search(row["notes"] or ""):  # notes can say "some copies": the writer reads them in full
            points += pts
            why.append(f"{label} (per the release notes)")
    if is_colored(_row_formats(row)):
        points += 8
        why.append("colored vinyl")
    n = limited_to(row["notes"], line)
    if n:
        points += max(5, round(30 - 6 * math.log10(n)))
        why.append(f"limited to {n:,} copies")
    if row["have"]:
        scarce = max(0, min(30, round(35 - 10 * math.log10(row["have"]))))
        if scarce:
            points += scarce
            why.append(f"only {row['have']:,} Discogs users have it")
    return points, why


def suggested_price(row) -> tuple[float | None, str | None]:
    prices = json.loads(row["prices"] or "{}")
    for grade in (NM, VGP):
        v = (prices.get(grade) or {}).get("value")
        if v:
            return round(v, 2), grade
    return None, None


def record_facts(row, extra: tuple[str, ...] = ()) -> dict:
    """One record as facts a script may state. `extra` adds fields a format is about (prices, counts)."""
    labels = json.loads(row["labels"] or "[]")
    f = {"artist": row["artist"], "title": row["title"], "pressing": pressing(_row_formats(row)),
         "pressing_year": row["year"] or None, "label": labels[0]["name"] if labels else None,
         "album_first_released": row["original_date"], "release_id": row["release_id"],
         "discogs_url": f"https://www.discogs.com/release/{row['release_id']}", "cover_url": row["cover_url"]}
    if "counts" in extra:
        f.update(discogs_have=row["have"], discogs_want=row["want"])
    if "rating" in extra and row["rating_count"]:
        f.update(discogs_rating=round(row["rating_avg"], 2), discogs_ratings=row["rating_count"])
    if "price" in extra:
        price, grade = suggested_price(row)
        f.update(suggested_price_usd=price, price_condition=grade,
                 cheapest_for_sale_usd=row["lowest_price"], copies_for_sale=row["num_for_sale"])
    if "limit" in extra:
        f["limited_to"] = limited_to(row["notes"], f["pressing"])
    return {k: v for k, v in f.items() if v not in (None, "")}


# ---------- sync ----------

def _stale(at: str | None, days: float, now: datetime) -> bool:
    return not at or datetime.fromisoformat(at) < now - timedelta(days=days)


def _cache_get(conn, key: str):
    row = conn.execute("SELECT body, fetched_at FROM api_cache WHERE key=?", (key,)).fetchone()
    return (json.loads(row["body"]), row["fetched_at"]) if row else (None, None)


def _cache_put(conn, key: str, body) -> None:
    conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES (?,?,?)",
                 (key, json.dumps(body), now_iso()))


def save_collection(conn, items: list[dict]) -> dict:
    """Upsert the collection list; records no longer in it are marked, not deleted (old ideas point at them)."""
    seen, added = set(), 0
    for it in items:
        b = it["basic_information"]
        seen.add(it["instance_id"])
        vals = dict(release_id=b["id"], master_id=b.get("master_id") or None, artist=artist_credit(b["artists"]),
                    artists=json.dumps([clean_artist(a.get("anv") or a["name"]) for a in b["artists"]]),
                    title=b["title"], year=b.get("year") or None, formats=json.dumps(b.get("formats") or []),
                    labels=json.dumps([{"name": l["name"], "catno": l.get("catno")} for l in b.get("labels") or []]),
                    genres=json.dumps(b.get("genres") or []), styles=json.dumps(b.get("styles") or []),
                    cover_url=b.get("cover_image") or b.get("thumb"), date_added=it.get("date_added"),
                    my_rating=it.get("rating") or 0, synced_at=now_iso())
        if conn.execute("SELECT 1 FROM vinyl_records WHERE instance_id=?", (it["instance_id"],)).fetchone():
            conn.execute(f"UPDATE vinyl_records SET {', '.join(f'{k}=?' for k in vals)}, in_collection=1 "
                         "WHERE instance_id=?", [*vals.values(), it["instance_id"]])
        else:
            conn.execute(f"INSERT INTO vinyl_records (instance_id, {', '.join(vals)}) "
                         f"VALUES (?, {', '.join('?' * len(vals))})", [it["instance_id"], *vals.values()])
            added += 1
    gone = 0
    if seen:  # an empty answer is more likely an error than an emptied collection
        q = ",".join("?" * len(seen))
        gone = conn.execute(f"UPDATE vinyl_records SET in_collection=0 WHERE in_collection=1 AND instance_id NOT IN ({q})",
                            list(seen)).rowcount
    conn.commit()
    return {"records": len(seen), "added": added, "gone": gone}


def save_details(conn, release_id: int, r: dict | None) -> None:
    now = now_iso()
    if r is None:  # deleted on Discogs: don't ask again for a while
        conn.execute("UPDATE vinyl_records SET details_at=? WHERE release_id=?", (now, release_id))
        return
    c = r.get("community") or {}
    credits = [{"name": clean_artist(a.get("name", "")), "role": a.get("role")} for a in r.get("extraartists") or []]
    conn.execute(
        """UPDATE vinyl_records SET have=?, want=?, rating_avg=?, rating_count=?, lowest_price=?, num_for_sale=?,
                  released=?, country=?, notes=?, credits=?, details_at=? WHERE release_id=?""",
        (c.get("have"), c.get("want"), (c.get("rating") or {}).get("average"), (c.get("rating") or {}).get("count"),
         r.get("lowest_price"), r.get("num_for_sale"), r.get("released"), r.get("country"),
         (r.get("notes") or "").strip()[:4000] or None, json.dumps(credits[:40]), now, release_id))


def sync(conn, client=None, mb=None, now: datetime | None = None,
         max_lookups: int = config.VINYL_MAX_LOOKUPS_PER_RUN) -> dict:
    """Bring vinyl_records up to date within this run's budget. Returns what it did."""
    if not config.DISCOGS_TOKEN and client is None:
        return {"skipped": "DISCOGS_TOKEN is not set"}
    from .fetchers import discogs, musicbrainz
    client = client or discogs.DiscogsClient()
    now = now or datetime.now(timezone.utc)
    out: dict = {}
    _, fetched = _cache_get(conn, "vinyl:collection")
    if _stale(fetched, config.VINYL_COLLECTION_REFRESH_HOURS / 24, now):
        out.update(save_collection(conn, client.collection()))
        try:
            _cache_put(conn, "vinyl:value", client.collection_value())
        except Exception as exc:  # a nice-to-have
            log.warning("Discogs collection value failed: %s", exc)
        _cache_put(conn, "vinyl:collection", {"at": now_iso()})
        conn.commit()
    budget = max_lookups
    detail_cut = (now - timedelta(days=config.VINYL_DETAILS_REFRESH_DAYS)).isoformat()
    ids = [r["release_id"] for r in conn.execute(
        """SELECT release_id FROM vinyl_records WHERE in_collection=1 AND (details_at IS NULL OR details_at < ?)
           GROUP BY release_id ORDER BY MAX(details_at IS NULL) DESC, MIN(details_at) LIMIT ?""", (detail_cut, budget))]
    for rid in ids:
        save_details(conn, rid, client.release(rid))
        conn.commit()
    budget -= len(ids)
    price_cut = (now - timedelta(days=config.VINYL_PRICES_REFRESH_DAYS)).isoformat()
    pids = [r["release_id"] for r in conn.execute(
        """SELECT release_id FROM vinyl_records WHERE in_collection=1 AND (prices_at IS NULL OR prices_at < ?)
           GROUP BY release_id ORDER BY MAX(prices_at IS NULL) DESC, MIN(prices_at) LIMIT ?""",
        (price_cut, max(budget, 0)))]
    for rid in pids:
        p = client.price_suggestions(rid)
        conn.execute("UPDATE vinyl_records SET prices=?, prices_at=? WHERE release_id=?",
                     (json.dumps(p or {}), now_iso(), rid))
        conn.commit()
    out.update(details=len(ids), prices=len(pids), discogs_calls=client.calls)
    # The album's first release date (for anniversaries): once per album, at MusicBrainz's 1 request a second
    mb = mb or musicbrainz.MusicBrainzClient()
    todo = conn.execute("""SELECT artist, artists, title FROM vinyl_records WHERE in_collection=1 AND mb_at IS NULL
                           GROUP BY artist, title LIMIT ?""", (max_lookups // 2,)).fetchall()
    found = 0
    for r in todo:
        try:
            g = mb.find_release_group(main_artist(r), album_title(r["title"]))
        except Exception as exc:
            log.warning("MusicBrainz lookup failed for %s - %s: %s", r["artist"], r["title"], exc)
            break  # MusicBrainz is shedding load; the rest wait for the next run
        conn.execute("UPDATE vinyl_records SET original_date=?, mb_release_group=?, mb_at=? WHERE artist=? AND title=?",
                     (g and g["first_release_date"], g and g["id"], now_iso(), r["artist"], r["title"]))
        conn.commit()
        found += bool(g and g["first_release_date"])
    out.update(albums_dated=found, musicbrainz_calls=mb.calls)
    return out


# ---------- ideas ----------

def records(conn) -> list:
    return conn.execute("SELECT * FROM vinyl_records WHERE in_collection=1 ORDER BY date_added DESC").fetchall()


def _end_of_day(d: date) -> str:
    return datetime.combine(d, time(23, 59), TZ).astimezone(timezone.utc).isoformat()


def _month_end(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _idea(key: str, fmt: str, title: str, score: float, facts: dict, expires: date,
          post_by: datetime | None = None) -> dict:
    return {"key": f"vinyl:{key}", "kind": KIND, "title": title, "score": round(score, 1),
            "facts": {"format": fmt, **facts}, "expires_at": _end_of_day(expires),
            "post_by": post_by.astimezone(timezone.utc).isoformat() if post_by else None}


def week_of(d: date) -> str:
    week = d.isocalendar()
    return f"{week.year}-W{week.week:02d}"


def recently_featured(conn, today: date, days: int = config.VINYL_STORY_REPEAT_DAYS) -> set[int]:
    """Release ids picked for a story idea recently, so the next pick is a different record."""
    since = (datetime.combine(today, time(0), TZ) - timedelta(days=days)).astimezone(timezone.utc).isoformat()
    out = set()
    for r in conn.execute("SELECT facts FROM alerts WHERE kind=? AND created_at >= ?", (KIND, since)):
        rec = json.loads(r["facts"]).get("record")
        if rec:
            out.add(rec.get("release_id"))
    return out


def _daily_pick(rows: list, today: date, salt: str):
    """A stable choice for the day among the top candidates (so a re-run on the same day picks the same one)."""
    if not rows:
        return None
    h = int(hashlib.sha256(f"{salt}:{today}".encode()).hexdigest(), 16)
    return rows[h % len(rows)]


def unique_idea(rows: list, today: date) -> dict | None:
    scored = sorted(((uniqueness(r), r) for r in rows if r["details_at"]), key=lambda x: -x[0][0])
    seen, top = set(), []
    for (pts, why), r in scored:  # one pressing per album
        if (r["artist"], album_title(r["title"])) in seen or pts < 15:
            continue
        seen.add((r["artist"], album_title(r["title"])))
        top.append({**record_facts(r, ("counts", "limit")), "what_makes_it_unusual": why})
        if len(top) == config.VINYL_LIST_SIZE:
            break
    if len(top) < 3:
        return None
    return _idea(f"unique:{today:%Y-%m}", "vinyl_unique", f"Top {len(top)} most unique pressings in my collection",
                 62, {"records": top, "why": [f"Ranked by pressing details and how few Discogs users own them"]},
                 _month_end(today))


def value_idea(rows: list, today: date, value: dict | None) -> dict | None:
    priced = sorted((r for r in rows if suggested_price(r)[0]), key=lambda r: -suggested_price(r)[0])
    top, seen = [], set()
    for r in priced:
        if r["release_id"] in seen:
            continue
        seen.add(r["release_id"])
        top.append(record_facts(r, ("price", "counts")))
        if len(top) == config.VINYL_LIST_SIZE:
            break
    if len(top) < 3:
        return None
    facts = {"records": top, "prices_from": "Discogs' suggested prices from recent sales, by condition",
             "why": ["Ranked by Discogs' suggested Near Mint price"]}
    if value:
        facts["whole_collection_value"] = {k: value.get(k) for k in ("minimum", "median", "maximum")}
    return _idea(f"value:{today:%Y-%m}", "vinyl_value", f"My {len(top)} most valuable records", 64, facts,
                 _month_end(today))


def coveted_idea(rows: list, today: date) -> dict | None:
    wanted = sorted((r for r in rows if r["want"]), key=lambda r: (-r["want"], r["have"] or 0))
    top, seen = [], set()
    for r in wanted:
        if r["release_id"] in seen:
            continue
        seen.add(r["release_id"])
        top.append(record_facts(r, ("counts", "price")))
        if len(top) == config.VINYL_LIST_SIZE:
            break
    if len(top) < 3:
        return None
    return _idea(f"coveted:{today:%Y-%m}", "vinyl_coveted", f"The {len(top)} records in my collection people want most",
                 60, {"records": top, "why": ["Ranked by how many Discogs users have it on their want list"]},
                 _month_end(today))


def variants_idea(rows: list, today: date) -> dict | None:
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["artist"], r["master_id"] or fold(album_title(r["title"]))), []).append(r)
    multi = [g for g in groups.values() if len({r["release_id"] for r in g}) > 1 or len(g) > 1]
    if not multi:
        return None
    multi.sort(key=lambda g: (-len(g), -sum(r["want"] or 0 for r in g)))
    g = _daily_pick(multi[:4], today, "variants")
    recs = [record_facts(r, ("counts", "price", "limit")) for r in g]
    return _idea(f"variants:{today:%Y-%m}", "vinyl_variants",
                 f"{len(g)} pressings of {album_title(g[0]['title'])}: which one's the keeper?", 58,
                 {"records": recs, "why": [f"You own {len(g)} copies of this album"]}, _month_end(today))


def haul_idea(rows: list, today: date) -> dict | None:
    since = (today - timedelta(days=14)).isoformat()
    new = [r for r in rows if (r["date_added"] or "")[:10] >= since]
    if len(new) < 2:
        return None
    return _idea(f"haul:{week_of(today)}", "vinyl_haul", f"Vinyl haul: {min(len(new), 12)} new records", 61,
                 {"records": [{**record_facts(r, ("counts",)), "added": r["date_added"][:10]} for r in new[:12]],
                  "why": [f"{len(new)} records added in the last two weeks"]}, today + timedelta(days=10))


def numbers_idea(rows: list, today: date, value: dict | None) -> dict | None:
    if len(rows) < 5:
        return None
    by_artist: dict[str, int] = {}
    styles: dict[str, int] = {}
    colored = 0
    for r in rows:
        by_artist[main_artist(r)] = by_artist.get(main_artist(r), 0) + 1
        for s in json.loads(r["styles"] or "[]"):
            styles[s] = styles.get(s, 0) + 1
        colored += is_colored(_row_formats(r))
    dated = [r for r in rows if r["original_date"]]
    facts = {"record_count": len(rows),
             "most_collected_artists": [{"artist": a, "records": n} for a, n in
                                        sorted(by_artist.items(), key=lambda x: -x[1])[:5] if n > 1],
             "top_styles": [{"style": s, "records": n} for s, n in sorted(styles.items(), key=lambda x: -x[1])[:5]],
             "colored_vinyl_records": colored,
             "oldest_album": record_facts(min(dated, key=lambda r: r["original_date"])) if dated else None,
             "newest_addition": record_facts(rows[0]),
             "why": ["Your collection's own numbers from Discogs"]}
    if value:
        facts["discogs_estimated_value"] = {k: value.get(k) for k in ("minimum", "median", "maximum")}
    return _idea(f"numbers:{today:%Y-%m}", "vinyl_numbers", f"My vinyl collection by the numbers ({len(rows)} records)",
                 55, {k: v for k, v in facts.items() if v}, _month_end(today))


def _story_rows(rows: list, used: set[int]) -> list:
    out, seen = [], set()
    for r in rows:
        if r["release_id"] in used or r["release_id"] in seen or not r["details_at"]:
            continue
        seen.add(r["release_id"])
        out.append(r)
    return out


def story_idea(rec, fmt: str, key: str, today: date, expires: date, score: float, why: list[str],
               extra: dict | None = None, post_by: datetime | None = None) -> dict:
    a = album_title(rec["title"])
    titles = {"vinyl_lore": f"The story behind {rec['artist']}'s {a}",
              "vinyl_underappreciated": f"Underappreciated: {rec['artist']} – {a}",
              "vinyl_underrated": f"Underrated vinyl of the day: {rec['artist']} – {a}"}
    facts = {"record": record_facts(rec, ("counts", "rating", "limit")), "why": why, **(extra or {})}
    return _idea(key, fmt, (extra or {}).get("title") or titles[fmt], score, facts, expires, post_by)


def lore_idea(rows: list, used: set[int], today: date) -> dict | None:
    """A well-documented record (many owners = more written about it) you haven't told the story of lately."""
    pool = sorted(_story_rows(rows, used), key=lambda r: -(r["have"] or 0))[:8]
    r = _daily_pick(pool, today, "lore")
    if r is None:
        return None
    return story_idea(r, "vinyl_lore", f"lore:{week_of(today)}", today, today + timedelta(days=13), 60,
                      ["Storytelling: researched from Wikipedia, Discogs and MusicBrainz when you draft it"])


def underappreciated_idea(rows: list, used: set[int], today: date) -> dict | None:
    """Well rated by the people who own it, but owned by few of them."""
    have = sorted(r["have"] for r in rows if r["have"])
    if not have:
        return None
    median = have[len(have) // 2]
    pool = [r for r in _story_rows(rows, used) if r["rating_count"] and r["rating_count"] >= 5
            and (r["rating_avg"] or 0) >= 4.0 and (r["have"] or 0) <= median]
    pool.sort(key=lambda r: (-(r["rating_avg"] or 0), r["have"] or 0))
    r = _daily_pick(pool[:6], today, "underappreciated")
    if r is None:
        return None
    return story_idea(r, "vinyl_underappreciated", f"underappreciated:{week_of(today)}", today,
                      today + timedelta(days=13), 58,
                      [f"Rated {r['rating_avg']:.2f}/5 by {r['rating_count']} Discogs users, but only "
                       f"{r['have']:,} own it (your collection's median: {median:,})"])


def underrated_idea(rows: list, used: set[int], today: date) -> dict | None:
    """One a day: a quick spotlight on a good record that few people own."""
    pool = [r for r in _story_rows(rows, used) if (r["rating_avg"] or 0) >= 3.8 and r["have"]]
    pool.sort(key=lambda r: r["have"])
    r = _daily_pick(pool[:12], today, "underrated")
    if r is None:
        return None
    return story_idea(r, "vinyl_underrated", f"underrated:{today}", today, today + timedelta(days=1), 52,
                      [f"{r['have']:,} Discogs users own it; rated {r['rating_avg']:.2f}/5"])


def anniversary_ideas(rows: list, today: date) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        d = r["original_date"]
        if not d or len(d) != 10 or (r["artist"], d) in seen:
            continue
        first = date.fromisoformat(d)
        for years in MILESTONES:
            try:
                day = first.replace(year=first.year + years)
            except ValueError:  # Feb 29
                day = first.replace(year=first.year + years, day=28)
            if today <= day <= today + timedelta(days=ANNIVERSARY_LOOKAHEAD_DAYS):
                seen.add((r["artist"], d))
                title = f"{album_title(r['title'])} turns {years} on {fmt_date(day.isoformat())}"
                score = 66 + min(14, years)  # round numbers matter more: 10 and 20 years beat 1
                out.append(story_idea(
                    r, "vinyl_anniversary", f"anniversary:{r['mb_release_group'] or r['release_id']}:{years}", today,
                    day + timedelta(days=3), score,
                    [f"First released {fmt_date(d)}, {first.year} (MusicBrainz): post on or before the day"],
                    {"anniversary": {"years": years, "date": day.isoformat(), "first_released": d}, "title": title},
                    post_by=datetime.combine(day, time(12), TZ)))
    return out


def look_into_idea(conn, rows: list, today: date, client=None, taste=None) -> dict | None:
    """Three records to look into: albums on vinyl by artists you play a lot but don't own on vinyl yet.
    Your plays only choose the artists; they never go into the facts."""
    key = f"vinyl:look_into:{today:%Y-%m}"
    if conn.execute("SELECT 1 FROM alerts WHERE key=?", (key,)).fetchone():
        return None  # already raised this month: don't spend the searches again
    taste = taste if taste is not None else listening.load()
    if not taste.plays or (client is None and not config.DISCOGS_TOKEN):
        return None
    from .fetchers import discogs
    client = client or discogs.DiscogsClient()
    owned_artists = {listening.artist_key(a) for r in rows for a in json.loads(r["artists"] or "[]") or [r["artist"]]}
    owned_titles = {fold(album_title(r["title"])) for r in rows}
    picks = []
    for akey, _ in sorted(taste.plays.items(), key=lambda x: -x[1])[:25]:
        if akey in owned_artists:
            continue
        name = taste.names.get(akey) or akey
        best = None
        for m in client.search_vinyl(name, per_page=10):
            artist, _, title = (m.get("title") or "").partition(" - ")
            if listening.artist_key(clean_artist(artist)) != akey or fold(title) in owned_titles:
                continue
            c = m.get("community") or {}
            pull = (c.get("have") or 0) + (c.get("want") or 0)  # owned or wanted: a real release, not an oddity
            if (c.get("want") or 0) >= 30 and (best is None or pull > best["_pull"]):
                best = {**m, "community": c, "_title": title, "_pull": pull}
        if best:
            picks.append({"artist": clean_artist(best["title"].partition(" - ")[0]), "title": best["_title"],
                          "first_released_year": best.get("year") or None,
                          "discogs_have": best["community"].get("have"), "discogs_want": best["community"].get("want"),
                          "discogs_url": "https://www.discogs.com" + best["uri"] if best.get("uri") else None})
        if len(picks) == 3:
            break
    if len(picks) < 3:
        return None
    return _idea(f"look_into:{today:%Y-%m}", "vinyl_look_into", "Three records worth looking into", 57,
                 {"records": [{k: v for k, v in p.items() if v} for p in picks],
                  "why": ["Artists you play a lot with no vinyl in your collection yet; their most-owned album on vinyl"]},
                 _month_end(today))


def ideas(conn, today: date | None = None, client=None, taste=None) -> list[dict]:
    today = today or datetime.now(TZ).date()
    rows = records(conn)
    if not rows:
        return []
    value, _ = _cache_get(conn, "vinyl:value")
    used = recently_featured(conn, today)
    out = [unique_idea(rows, today), value_idea(rows, today, value), coveted_idea(rows, today),
           variants_idea(rows, today), haul_idea(rows, today), numbers_idea(rows, today, value)]
    # A story idea is raised once per period: skip the pick when it's already raised, so the day's other picks
    # aren't starved of records by a choice that would never be saved
    for builder, key in ((lore_idea, f"lore:{week_of(today)}"), (underappreciated_idea, f"underappreciated:{week_of(today)}"),
                         (underrated_idea, f"underrated:{today}")):
        if conn.execute("SELECT 1 FROM alerts WHERE key=?", (f"vinyl:{key}",)).fetchone():
            continue
        idea = builder(rows, used, today)
        if idea:
            used.add(idea["facts"]["record"]["release_id"])
            out.append(idea)
    out += anniversary_ideas(rows, today)
    try:
        out.append(look_into_idea(conn, rows, today, client, taste))
    except Exception as exc:
        log.warning("Records-to-look-into search failed: %s", exc)
    return [i for i in out if i]


def save_ideas(conn, new: list[dict]) -> list[int]:
    """Raise ideas whose key is new; existing ones are left exactly as they were."""
    fresh = [i for i in new if not conn.execute("SELECT 1 FROM alerts WHERE key=?", (i["key"],)).fetchone()]
    return save_alerts(conn, fresh)


def refresh(conn, now: datetime | None = None) -> dict:
    """The pipeline step: sync within budget, then raise this period's ideas. Never raises."""
    out = {}
    try:
        out["sync"] = sync(conn, now=now)
    except Exception as exc:
        log.warning("Discogs sync failed: %s", exc)
        out["sync_error"] = str(exc)
    try:
        out["new_ideas"] = len(save_ideas(conn, ideas(conn)))
    except Exception as exc:
        log.exception("Vinyl ideas failed: %s", exc)
        out["ideas_error"] = str(exc)
    return out


def make_idea(conn, instance_id: int, fmt: str, today: date | None = None) -> int:
    """'Make a video idea' from one record in the Vinyl tab. Returns the alert id (an existing one for the same
    record and format today)."""
    if fmt not in MANUAL_FORMATS:
        raise ValueError(f"format must be one of {', '.join(MANUAL_FORMATS)}")
    rec = conn.execute("SELECT * FROM vinyl_records WHERE instance_id=?", (instance_id,)).fetchone()
    if rec is None:
        raise KeyError(instance_id)
    today = today or datetime.now(TZ).date()
    idea = story_idea(rec, fmt, f"{fmt.removeprefix('vinyl_')}:{rec['release_id']}:picked-{today}", today,
                      today + timedelta(days=30), 60, ["You picked this record"])
    save_ideas(conn, [idea])
    return conn.execute("SELECT id FROM alerts WHERE key=?", (idea["key"],)).fetchone()["id"]


# ---------- the Vinyl tab ----------

def overview(conn) -> dict:
    rows = records(conn)
    value, _ = _cache_get(conn, "vinyl:value")
    _, synced = _cache_get(conn, "vinyl:collection")
    out = []
    for r in rows:
        pts, why = uniqueness(r) if r["details_at"] else (None, [])
        price, grade = suggested_price(r)
        out.append({"instance_id": r["instance_id"], "release_id": r["release_id"], "artist": r["artist"],
                    "title": r["title"], "pressing": pressing(_row_formats(r)), "year": r["year"],
                    "album_first_released": r["original_date"], "cover_url": r["cover_url"],
                    "date_added": r["date_added"], "have": r["have"], "want": r["want"],
                    "rating_avg": r["rating_avg"], "rating_count": r["rating_count"], "suggested_price": price,
                    "price_condition": grade, "lowest_price": r["lowest_price"], "unique_score": pts,
                    "unique_why": why, "styles": json.loads(r["styles"] or "[]"),
                    "url": f"https://www.discogs.com/release/{r['release_id']}"})
    pending = conn.execute("""SELECT COUNT(*) FROM vinyl_records WHERE in_collection=1
                              AND (details_at IS NULL OR prices_at IS NULL)""").fetchone()[0]
    return {"enabled": bool(config.DISCOGS_TOKEN), "synced_at": synced, "value": value, "records": out,
            "pending_lookups": pending, "formats": FORMAT_LABEL, "manual_formats": list(MANUAL_FORMATS)}
