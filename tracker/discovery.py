"""Artist discovery and momentum, step 7 (docs/agent-notes/music-roadmap.md M7). Experimental: shown as such.

Candidates (artists you don't follow yet), rebuilt weekly from cached lookups:
- Last.fm similar artists of your most-played watchlist artists;
- Last.fm's top artists for your niche tags (config.DISCOVERY_TAGS): candidate lists, not proof of anything;
- guests on your artists' releases ("Static (feat. Echo Bay)" in the catalogs, tracker/catalog.py).

Daily snapshots (`artist_metrics`) for the watchlist and the candidates, a slice per news check: Deezer fans, Last.fm
listeners, ListenBrainz listens (supplementary), and outlets that mentioned them in the last 7 days (no API call).

Momentum is growth against the artist's own baseline, never audience size: a signal counts only with at least
MOMENTUM_MIN_OBS observations spanning MOMENTUM_MIN_DAYS, from a base of at least MOMENTUM_MIN_BASE; an artist is a
breakout candidate only when two signals agree (ListenBrainz never counts toward that). Nothing is called trending
otherwise. Until a week of snapshots exists, momentum is "collecting" everywhere, by design.

Nothing from listening history leaves the PC except the watchlist names sent to Last.fm for similar-artist lookups.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from . import config, listening, watchlist
from .catalog import FEAT
from .detectors import fmt_date, save_alerts
from .music import clean_title

log = logging.getLogger(__name__)
TZ = ZoneInfo(config.TIMEZONE)
SIGNALS = [("deezer", "fans", "Deezer fans"), ("lastfm", "listeners", "Last.fm listeners"),
           ("listenbrainz", "listens", "ListenBrainz listens")]
COUNTED = {("deezer", "fans"), ("lastfm", "listeners"), ("news", "mentions")}  # toward the two-signal rule
ACTIONS = ("watch", "adjacent", "dismiss", "restore")


class DiscoveryError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _day(now: datetime) -> str:
    return now.astimezone(TZ).date().isoformat()


# ---------- cache ----------

def cached(conn, key: str, fetch, max_age: timedelta, now: datetime):
    """A stored lookup if it's fresh enough, else fetch() (stored only when it succeeds)."""
    row = conn.execute("SELECT body, fetched_at FROM api_cache WHERE key=?", (key,)).fetchone()
    if row and datetime.fromisoformat(row["fetched_at"]) > now - max_age:
        return json.loads(row["body"])
    body = fetch()
    conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES (?,?,?)",
                 (key, json.dumps(body), now.isoformat()))
    conn.commit()
    return body


def _marker(conn, key: str) -> datetime | None:
    row = conn.execute("SELECT fetched_at FROM api_cache WHERE key=?", (key,)).fetchone()
    return datetime.fromisoformat(row["fetched_at"]) if row else None


def _mark(conn, key: str, now: datetime, body=None) -> None:
    conn.execute("INSERT OR REPLACE INTO api_cache (key, body, fetched_at) VALUES (?,?,?)",
                 (key, json.dumps(body), now.isoformat()))


# ---------- candidates ----------

def guests(title: str) -> list[str]:
    """"Static (feat. Echo Bay & Glass Hours)" -> ["Echo Bay", "Glass Hours"]."""
    out = []
    for m in FEAT.finditer(title or ""):
        for name in re.split(r"\s*(?:,|&|\band\b|\bx\b)\s*", m.group(1) or m.group(2) or "", flags=re.I):
            name = name.strip(" .")
            if len(name) >= 2:
                out.append(name)
    return out


def credit_of(name: str, known: set[str]) -> bool:
    """"Kilo Moon & Echo Bay" or "Nova Vale, Kilo Moon": a joint credit with someone you follow, not a new artist."""
    parts = [p for p in re.split(r"\s*(?:,|&|\bx\b|\band\b)\s*", name, flags=re.I) if p.strip()]
    return len(parts) > 1 and any(listening.artist_key(p) in known for p in parts)


def _seeds(conn, taste: listening.Taste) -> list:
    rows = conn.execute("""SELECT * FROM artist_identities WHERE muted=0 AND tier IN ('core', 'watch')""").fetchall()
    return sorted(rows, key=lambda r: (-taste.plays.get(r["key"], 0), r["name"]))[:config.DISCOVERY_SEEDS]


def gather(conn, lastfm, now: datetime) -> dict[str, dict]:
    """Every candidate with its evidence. Artists already in the watchlist (any tier, muted too) are skipped."""
    known = {r["key"] for r in conn.execute("SELECT key FROM artist_identities")}
    found: dict[str, dict] = {}
    week = timedelta(days=config.DISCOVERY_REFRESH_DAYS)

    def add(name: str, evidence: dict, url: str | None = None, mbid: str | None = None):
        key = listening.artist_key(name)
        if not key or key in known or credit_of(name, known):
            return
        c = found.setdefault(key, {"key": key, "name": name, "evidence": [], "url": url, "mbid": mbid})
        c["url"] = c["url"] or url
        c["mbid"] = c["mbid"] or mbid
        c["evidence"].append(evidence)

    for seed in _seeds(conn, listening.load()):
        for s in cached(conn, f"lastfm:similar:{seed['key']}",
                        lambda: lastfm.similar(seed["name"], config.DISCOVERY_SIMILAR_PER_SEED), week, now):
            add(s["name"], {"kind": "similar", "of": seed["name"], "weight": round(s["match"], 2)}, s["url"], s["mbid"])
    for tag in config.DISCOVERY_TAGS:
        for t in cached(conn, f"lastfm:tag:{tag}", lambda: lastfm.tag_top(tag, config.DISCOVERY_TAG_ARTISTS), week, now):
            add(t["name"], {"kind": "tag", "of": tag, "detail": t["rank"], "weight": 0.3}, t["url"], t["mbid"])
    for r in conn.execute("""SELECT DISTINCT r.title, w.name FROM releases r JOIN artist_identities w ON w.id = r.artist_id
                             WHERE w.muted = 0 AND w.tier IN ('core', 'watch', 'adjacent', 'breakout')"""):
        song = clean_title(re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", FEAT.sub("", r["title"])))  # remixes are one song
        for g in guests(r["title"]):
            add(g, {"kind": "feature", "of": r["name"], "detail": song, "weight": 0.6})
    return found


def evidence_score(evidence: list[dict]) -> float:
    """More independent reasons beat one strong one: similar to several of your artists, a guest spot, a tag."""
    similar = sum(e["weight"] for e in evidence if e["kind"] == "similar")
    tags = len({e["of"] for e in evidence if e["kind"] == "tag"})
    features = len({(e["of"], e["detail"]) for e in evidence if e["kind"] == "feature"})
    return round(similar + 0.3 * tags + 0.6 * features, 2)


def refresh(conn, lastfm=None, now: datetime | None = None, force: bool = False) -> dict:
    """Weekly: rebuild the candidate list. Your accepted and dismissed calls are kept."""
    from .fetchers.lastfm import LastfmClient
    now = now or _now()
    last = _marker(conn, "discovery:refreshed")
    if not force and last and last > now - timedelta(days=config.DISCOVERY_REFRESH_DAYS):
        return {"refreshed": False}
    found = gather(conn, lastfm or LastfmClient(), now)
    best = sorted(found.values(), key=lambda c: -evidence_score(c["evidence"]))[:config.DISCOVERY_MAX_CANDIDATES]
    new = 0
    for c in best:
        cur = conn.execute("""UPDATE artist_candidates SET evidence=?, last_seen=?,
                                  mbid=COALESCE(mbid, ?), lastfm_url=COALESCE(lastfm_url, ?) WHERE key=?""",
                           (json.dumps(c["evidence"]), now.isoformat(), c["mbid"], c["url"], c["key"]))
        if not cur.rowcount:
            conn.execute("""INSERT INTO artist_candidates (key, name, evidence, mbid, lastfm_url, first_seen, last_seen)
                            VALUES (?,?,?,?,?,?,?)""", (c["key"], c["name"], json.dumps(c["evidence"]), c["mbid"],
                                                        c["url"], now.isoformat(), now.isoformat()))
            new += 1
    _mark(conn, "discovery:refreshed", now, {"candidates": len(best), "new": new})
    conn.commit()
    return {"refreshed": True, "candidates": len(best), "new": new}


# ---------- daily snapshots ----------

def _subjects(conn) -> list[dict]:
    """Who gets snapshotted: the watchlist and the current candidates (not dismissed)."""
    out = [{"key": r["key"], "name": r["name"], "mbid": r["mbid"], "deezer_id": r["deezer_id"], "candidate": None}
           for r in conn.execute("SELECT * FROM artist_identities WHERE muted=0 AND tier IN ('core', 'watch', 'adjacent', 'breakout')")]
    have = {s["key"] for s in out}
    since = (_now() - timedelta(days=2 * config.DISCOVERY_REFRESH_DAYS)).isoformat()
    for r in conn.execute("SELECT * FROM artist_candidates WHERE status='new' AND last_seen >= ?", (since,)):
        if r["key"] not in have:
            out.append({"key": r["key"], "name": r["name"], "mbid": r["mbid"], "deezer_id": r["deezer_id"], "candidate": r["id"]})
    return out


def _put(conn, key: str, provider: str, metric: str, value, day: str) -> None:
    if value is not None:
        conn.execute("INSERT OR REPLACE INTO artist_metrics (artist_key, provider, metric, value, observed_on) "
                     "VALUES (?,?,?,?,?)", (key, provider, metric, int(value), day))


def snapshot(conn, subject: dict, lastfm, deezer, lb, day: str) -> None:
    info = lastfm.info(subject["name"])
    if info:
        _put(conn, subject["key"], "lastfm", "listeners", info["listeners"], day)
        _put(conn, subject["key"], "lastfm", "playcount", info["playcount"], day)
    mbid = subject["mbid"] or (info or {}).get("mbid")
    deezer_id = subject["deezer_id"]
    if subject["candidate"] is not None:
        if deezer_id is None and not _marker(conn, f"deezer:find:{subject['key']}"):
            found = deezer.find_artist(subject["name"])
            deezer_id = found["id"] if found else None
            _mark(conn, f"deezer:find:{subject['key']}", _now())
        conn.execute("""UPDATE artist_candidates SET deezer_id=COALESCE(deezer_id, ?), mbid=COALESCE(mbid, ?),
                            tags=CASE WHEN ? != '[]' THEN ? ELSE tags END, lastfm_url=COALESCE(lastfm_url, ?) WHERE id=?""",
                     (deezer_id, mbid, *([json.dumps((info or {}).get("tags") or [])] * 2), (info or {}).get("url"),
                      subject["candidate"]))
    if deezer_id:
        _put(conn, subject["key"], "deezer", "fans", deezer.fans(deezer_id), day)
    if mbid:
        got = lb.listens(mbid)
        if got:
            _put(conn, subject["key"], "listenbrainz", "listens", got["listens"], day)


def snapshot_due(conn, now: datetime | None = None, limit: int | None = None, clients=None) -> int:
    """Snapshot the next `limit` subjects not yet done today. Stops quietly on a rate limit or network error."""
    from .fetchers.deezer import DeezerClient
    from .fetchers.lastfm import LastfmClient
    from .fetchers.listenbrainz import ListenBrainzClient
    now = now or _now()
    day = _day(now)
    limit = config.METRICS_ARTISTS_PER_CHECK if limit is None else limit
    lastfm, deezer, lb = clients or (LastfmClient(), DeezerClient(), ListenBrainzClient())
    done = 0
    for s in _subjects(conn):
        if done >= limit:
            break
        if (conn.execute("SELECT body FROM api_cache WHERE key=?", (f"metrics:{s['key']}",)).fetchone() or {"body": "null"})["body"] == json.dumps(day):
            continue
        try:
            snapshot(conn, s, lastfm, deezer, lb, day)
        except (requests.RequestException, ValueError) as exc:
            log.warning("Artist snapshots stopped after %d: %s", done, exc)
            break
        _mark(conn, f"metrics:{s['key']}", now, day)
        conn.commit()
        done += 1
    conn.commit()
    return done


def mentions(conn, now: datetime | None = None) -> int:
    """Once a day: outlets that mentioned each subject in the last 7 days (stored articles only, no API)."""
    now = now or _now()
    day = _day(now)
    if (conn.execute("SELECT body FROM api_cache WHERE key='mentions:day'").fetchone() or {"body": "null"})["body"] == json.dumps(day):
        return 0
    since = (now - timedelta(days=7)).isoformat()
    counts: dict[str, set] = {}
    for r in conn.execute("""SELECT w.key, a.source_id FROM article_artists x JOIN articles a ON a.id = x.article_id
                             JOIN artist_identities w ON w.id = x.artist_id
                             WHERE x.confidence IN ('high', 'medium') AND COALESCE(a.published_at, a.fetched_at) >= ?""",
                          (since,)):
        counts.setdefault(r["key"], set()).add(r["source_id"])
    cands = [dict(r) | {"aliases": "[]", "common_word": 0, "common_word_override": None, "ambiguous_name": 1}
             for r in conn.execute("SELECT id, key, name FROM artist_candidates WHERE status='new'")]
    if cands:  # titles only, and a name that's also an ordinary word needs a music word next to it
        arts = conn.execute("SELECT title, source_id FROM articles WHERE COALESCE(published_at, fetched_at) >= ?",
                            (since,)).fetchall()
        texts = [(a["title"], "") for a in arts]
        for c in cands:
            c["common_word"] = int(watchlist.is_common_word(c["name"], texts))
        matcher = watchlist.Matcher(cands)
        by_id = {c["id"]: c for c in cands}
        for a in arts:
            for h in matcher.match(a["title"], None):
                counts.setdefault(by_id[h["artist_id"]]["key"], set()).add(a["source_id"])
    n = 0
    for s in _subjects(conn):
        _put(conn, s["key"], "news", "mentions", len(counts.get(s["key"], ())), day)
        n += 1
    _mark(conn, "mentions:day", now, day)
    conn.commit()
    return n


def step(conn, now: datetime | None = None) -> dict:
    """The news job's discovery step: the weekly refresh when due, today's mention counts, a slice of snapshots."""
    now = now or _now()
    out = {"refresh": None, "snapshots": 0}
    if not config.LAST_FM_API_KEY:
        return {**out, "skipped": "LAST_FM_API_KEY isn't set"}
    try:
        out["refresh"] = refresh(conn, now=now)
    except (requests.RequestException, ValueError) as exc:
        log.warning("Discovery refresh failed: %s", exc)
    mentions(conn, now)
    out["snapshots"] = snapshot_due(conn, now)
    return out


# ---------- momentum ----------

def _series(conn, keys: list[str] | None) -> dict[str, dict[tuple, list]]:
    since = (_now() - timedelta(days=45)).date().isoformat()
    sql = "SELECT * FROM artist_metrics WHERE observed_on >= ?"
    params: list = [since]
    if keys is not None:
        if not keys:
            return {}
        sql += f" AND artist_key IN ({','.join('?' * len(keys))})"
        params += keys
    out: dict[str, dict[tuple, list]] = {}
    for r in conn.execute(sql + " ORDER BY observed_on", params):
        out.setdefault(r["artist_key"], {}).setdefault((r["provider"], r["metric"]), []).append((r["observed_on"], r["value"]))
    return out


def _span(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def signal(points: list[tuple[str, int]], provider: str, metric: str, label: str) -> dict:
    """One series judged by the sample-size rules: collecting, too small, or a weekly growth rate."""
    out = {"provider": provider, "metric": metric, "label": label, "observations": len(points),
           "counts": (provider, metric) in COUNTED, "ready": False, "growing": False}
    if points:
        out["latest"] = points[-1][1]
    if len(points) < config.MOMENTUM_MIN_OBS or _span(points[0][0], points[-1][0]) < config.MOMENTUM_MIN_DAYS:
        days = _span(points[0][0], points[-1][0]) if points else 0
        return {**out, "status": "collecting", "why": f"{label}: {len(points)} observation(s) over {days} day(s) so far"}
    first, last = points[0][1], points[-1][1]
    if first < config.MOMENTUM_MIN_BASE.get((provider, metric), 1):
        return {**out, "status": "too_small", "why": f"{label}: under {config.MOMENTUM_MIN_BASE[(provider, metric)]:,} to start from"}
    days = _span(points[0][0], points[-1][0])
    weekly = (last / first - 1) * 7 / days
    growing = weekly >= config.MOMENTUM_GROWTH_PER_WEEK
    return {**out, "status": "ready", "ready": True, "growing": growing, "weekly_growth": round(weekly, 4),
            "first": first, "days": days,
            "why": f"{label} {'+' if weekly >= 0 else ''}{weekly * 100:.1f}% a week ({first:,} → {last:,} in {days} days)"}


def mention_signal(points: list[tuple[str, int]]) -> dict:
    """Outlets this week vs. a week earlier (the snapshot closest to 7 days before the latest)."""
    out = {"provider": "news", "metric": "mentions", "label": "Outlets", "counts": True, "ready": False,
           "growing": False, "observations": len(points)}
    if not points:
        return {**out, "status": "collecting", "why": "Outlets: no counts yet"}
    latest_day, latest = points[-1]
    earlier = [p for p in points if _span(p[0], latest_day) >= 7]
    out["latest"] = latest
    if not earlier:
        return {**out, "status": "collecting", "why": f"Outlets: {latest} this week; no week-earlier count yet"}
    before = earlier[-1][1]
    growing = latest >= config.MOMENTUM_MIN_MENTIONS and latest > before
    return {**out, "status": "ready", "ready": True, "growing": growing,
            "why": f"{latest} outlet{'s' if latest != 1 else ''} in the last 7 days (vs {before} a week earlier)"}


def momentum(conn, keys: list[str] | None = None) -> dict[str, dict]:
    """artist key -> {ready, signals, growing, two_signals, value (0-100 or None), summary}."""
    out = {}
    for key, series in _series(conn, keys).items():
        sigs = [signal(series.get((p, m), []), p, m, label) for p, m, label in SIGNALS]
        sigs.append(mention_signal(series.get(("news", "mentions"), [])))
        counted = [s for s in sigs if s["ready"] and s["counts"] and s["growing"]]
        ready = any(s["ready"] for s in sigs)
        best = max((s.get("weekly_growth") or 0 for s in counted), default=0)
        value = None
        if ready:
            value = 85 + min(15, round(best * 100)) if len(counted) >= 2 else 60 if counted else 30
        summary = "; ".join(s["why"] for s in sigs if s["ready"] and s["growing"]) or (
            "Steady: no signal growing" if ready else "Collecting: needs a week of daily snapshots")
        out[key] = {"ready": ready, "signals": sigs, "growing": len(counted), "two_signals": len(counted) >= 2,
                    "value": value, "summary": summary}
    return out


# ---------- the Discover view ----------

def why_lines(evidence: list[dict], mom: dict | None = None) -> list[str]:
    """Plain reasons from the evidence (facts a video may state), strongest first."""
    lines = []
    similar = sorted({e["of"] for e in evidence if e["kind"] == "similar"})
    if similar:
        lines.append(f"Similar to {', '.join(similar[:3])}{' and more' if len(similar) > 3 else ''} on Last.fm")
    for of, song in dict.fromkeys((e["of"], e["detail"]) for e in evidence if e["kind"] == "feature"):
        lines.append(f"Featured on {of}'s “{song}”")
    tags = [t for _, t in sorted({(e["detail"], e["of"]) for e in evidence if e["kind"] == "tag"})]
    if tags:
        lines.append(f"One of Last.fm's top {' and '.join(tags[:2])} artists")
    if mom and mom["two_signals"]:
        lines.append(f"Growing on two signals: {mom['summary']}")
    return list(dict.fromkeys(lines))


def listing(conn, status: str = "new") -> dict:
    rows = conn.execute("SELECT * FROM artist_candidates WHERE status=? ORDER BY name", (status,)).fetchall()
    moms = momentum(conn, [r["key"] for r in rows])
    latest = {}
    for r in conn.execute("""SELECT m.* FROM artist_metrics m JOIN (SELECT artist_key, provider, metric, MAX(observed_on) AS d
                                 FROM artist_metrics GROUP BY artist_key, provider, metric) l
                             ON l.artist_key = m.artist_key AND l.provider = m.provider AND l.metric = m.metric
                                AND l.d = m.observed_on"""):
        latest.setdefault(r["artist_key"], {})[f"{r['provider']}_{r['metric']}"] = r["value"]
    out = []
    for r in rows:
        ev = json.loads(r["evidence"])
        mom = moms.get(r["key"])
        out.append({"id": r["id"], "name": r["name"], "status": r["status"], "score": evidence_score(ev),
                    "why": why_lines(ev, mom), "tags": json.loads(r["tags"]), "lastfm_url": r["lastfm_url"],
                    "similar_to": sorted({e["of"] for e in ev if e["kind"] == "similar"}),
                    "metrics": latest.get(r["key"], {}), "momentum": mom, "first_seen": r["first_seen"],
                    "breakout": bool(mom and mom["two_signals"])})
    out.sort(key=lambda c: (not c["breakout"], -c["score"], c["name"]))
    last = _marker(conn, "discovery:refreshed")
    return {"candidates": out, "refreshed_at": last.isoformat() if last else None,
            "enabled": bool(config.LAST_FM_API_KEY), "rules": {
                "min_observations": config.MOMENTUM_MIN_OBS, "min_days": config.MOMENTUM_MIN_DAYS,
                "growth_per_week": config.MOMENTUM_GROWTH_PER_WEEK, "min_mentions": config.MOMENTUM_MIN_MENTIONS}}


def decide(conn, candidate_id: int, action: str, now: datetime | None = None) -> dict:
    """watch / adjacent: add to the watchlist (articles about them start matching); dismiss: hide; restore: undo."""
    if action not in ACTIONS:
        raise DiscoveryError(f"action must be one of {', '.join(ACTIONS)}")
    row = conn.execute("SELECT * FROM artist_candidates WHERE id=?", (candidate_id,)).fetchone()
    if row is None:
        raise KeyError(candidate_id)
    now = now or _now()
    if action in ("watch", "adjacent"):
        artist_id = watchlist.add(conn, row["name"], action)
        conn.execute("""UPDATE artist_identities SET origin='related', mbid=COALESCE(mbid, ?), deezer_id=COALESCE(deezer_id, ?)
                        WHERE id=?""", (row["mbid"], row["deezer_id"], artist_id))
    status = {"watch": "accepted", "adjacent": "accepted", "dismiss": "dismissed", "restore": "new"}[action]
    conn.execute("UPDATE artist_candidates SET status=?, decided_at=? WHERE id=?",
                 (status, None if status == "new" else now.isoformat(), candidate_id))
    conn.commit()
    return {"id": candidate_id, "status": status}


def make_idea(conn, candidate_ids: list[int], now: datetime | None = None) -> int:
    """An "Artists to watch" video idea from 2-8 candidates (any status), with the evidence as its facts."""
    ids = list(dict.fromkeys(int(i) for i in candidate_ids))
    if not 2 <= len(ids) <= 8:
        raise DiscoveryError("pick 2 to 8 artists")
    rows = [conn.execute("SELECT * FROM artist_candidates WHERE id=?", (i,)).fetchone() for i in ids]
    if any(r is None for r in rows):
        raise KeyError("candidate")
    rows.sort(key=lambda r: (-evidence_score(json.loads(r["evidence"])), r["name"]))  # best evidenced first
    now = now or _now()
    moms = momentum(conn, [r["key"] for r in rows])
    items, because = [], []
    for r in rows:
        ev = json.loads(r["evidence"])
        lines = why_lines(ev, moms.get(r["key"]))
        items.append({"artist": r["name"], "why": lines[0] if lines else "On my radar"})
        because += [e["of"] for e in ev if e["kind"] in ("similar", "feature")]
    because = list(dict.fromkeys(because))[:3]
    facts = {"format": "emerging_artists", "artists_to_watch": items, "artist_count": len(items), "because": because,
             "momentum": {"two_signals": all((moms.get(r["key"]) or {}).get("two_signals") for r in rows)},
             "made_on": fmt_date(_day(now))}
    key = "emerging:" + hashlib.sha1("|".join(sorted(r["key"] for r in rows)).encode()).hexdigest()[:12]
    save_alerts(conn, [{"key": key, "kind": "emerging_artists",
                        "title": "Artists to watch: " + ", ".join(i["artist"] for i in items[:3])
                                 + (f" +{len(items) - 3}" if len(items) > 3 else ""),
                        "score": 60, "facts": facts, "post_by": (now + timedelta(days=3)).isoformat(),
                        "expires_at": (now + timedelta(days=21)).isoformat()}])
    return conn.execute("SELECT id FROM alerts WHERE key=?", (key,)).fetchone()["id"]
