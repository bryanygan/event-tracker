"""Local dashboard API + static file server for the React app in dashboard/dist.

Start with:  .venv\\Scripts\\python -m tracker.api
Then open:   http://127.0.0.1:8765
Bound to 127.0.0.1 only, so nothing on your network can reach it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from contextlib import closing
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import (angles, config, context, db, drafts, listening, llm, music, opportunities, planner, posts, publish, remote,
               slides, trends, watchlist)
from .scoring import upcoming_events

log = logging.getLogger(__name__)
app = FastAPI(title="Philly Events Tracker")
DIST = config.ROOT / "dashboard" / "dist"

# This API is only for the dashboard on this PC. Without these checks any website open in your browser could
# POST to it (run the pipeline, publish, spend web lookups) or, via DNS rebinding, read it.
# (tests/conftest.py adds "testserver", the name FastAPI's TestClient sends.)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_BODY_BYTES = 12_000_000


def _hostname(value: str) -> str:
    try:
        return urlsplit(value if "//" in value else f"//{value}").hostname or ""
    except ValueError:  # e.g. "Host: [" -> refused as unknown, not a 500
        return ""


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = _hostname(request.headers.get("host", ""))
    # Your phone, through the Cloudflare Tunnel: only with a valid Cloudflare Access login (see tracker/remote.py).
    phone = remote.enabled() and host == config.REMOTE_HOST
    if host not in LOCAL_HOSTS and not phone:
        return JSONResponse({"detail": "unknown host"}, status_code=403)
    email = None
    if phone:
        try:  # off the event loop: the first request after a key rotation fetches the keys from Cloudflare
            email = await run_in_threadpool(remote.verify, request.headers.get(remote.HEADER, ""))
        except remote.RemoteAuthError as exc:
            log.warning("Refused a request for %s: %s", host, exc)
            return JSONResponse({"detail": "Cloudflare Access login required"}, status_code=403)
    write = request.method not in ("GET", "HEAD", "OPTIONS")
    if write:
        origin = request.headers.get("origin")
        allowed = {config.REMOTE_HOST} if phone else LOCAL_HOSTS
        # Through the tunnel the browser always sends Origin on a write; require it there.
        if (phone and not origin) or (origin and _hostname(origin) not in allowed) \
                or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        # Every write the dashboard makes is a small JSON body (slides with photos are the biggest). No length
        # header means no body (e.g. DELETE), unless it's chunked, which the dashboard never sends.
        size = request.headers.get("content-length", "0")
        if "transfer-encoding" in request.headers or not size.isdigit() or int(size) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
    response = await call_next(request)
    if phone and write:
        try:
            with closing(conn()) as c:
                remote.record(c, email, request.method, request.url.path, response.status_code)
        except Exception:  # the edit itself already happened; don't turn it into an error
            log.exception("Couldn't record a phone edit")
    return response

# One background job at a time (a pipeline run or a draft regeneration), so the
# N95 never runs two LLM generations at once.
_job_lock = threading.Lock()
_job = {"name": None, "started": None, "error": None, "finished": None,
        "alert_id": None, "step": None, "estimate": None}  # draft jobs: which alert, what's happening, ~seconds
_job_t0 = 0.0
# How long one model generation took last time (per provider), so the progress bar's estimate matches this PC
_gen_seconds = {"ollama": 60.0, "claude": 8.0}
OLLAMA_START_SECONDS = 15


def conn():
    return db.connect()


def get_db():
    """One connection per request, always closed (open SQLite handles lock the file on Windows)."""
    c = conn()
    try:
        yield c
    finally:
        c.close()


def _start_job(name: str, fn, alert_id: int | None = None, step: str | None = None,
               estimate: float | None = None) -> dict:
    global _job_t0
    if not _job_lock.acquire(blocking=False):
        raise HTTPException(409, f"Busy: {_job['name']} is still running")
    _job_t0 = time.monotonic()
    _job.update(name=name, started=datetime.now().isoformat(timespec="seconds"), error=None, finished=None,
                alert_id=alert_id, step=step, estimate=estimate)

    def worker():
        try:
            fn()
        except Exception as exc:
            log.exception("Job %s failed", name)
            _job["error"] = str(exc)
        finally:
            _job["finished"] = datetime.now().isoformat(timespec="seconds")
            _job_lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return {**_job, "running": True, "elapsed": 0.0}  # even if a quick job already finished; the next poll says so


@app.get("/api/job")
def job_status():
    running = _job_lock.locked()
    # elapsed from the server's clock, so the bar is right even if the phone's clock or time zone differs
    return {**_job, "running": running, "elapsed": round(time.monotonic() - _job_t0, 1) if running else None}


@app.get("/api/summary")
def summary(c=Depends(get_db)):
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    last = c.execute("SELECT * FROM runs WHERE source != 'news' ORDER BY id DESC LIMIT 1").fetchone()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()  # same format as posted_at
    return {
        "upcoming_events": c.execute("SELECT COUNT(*) FROM events WHERE active=1 AND local_date>=?",
                                     (today,)).fetchone()[0],
        "open_alerts": c.execute("SELECT COUNT(*) FROM alerts WHERE status!='dismissed'").fetchone()[0],
        "pending_drafts": c.execute("SELECT COUNT(*) FROM drafts WHERE status='pending'").fetchone()[0],
        "last_run": dict(last) if last else None,
        "model": config.CLAUDE_MODEL if llm.uses_claude() else config.OLLAMA_MODEL,
        "drafting": llm.claude_usage(c),
        "has_tm_key": bool(config.TICKETMASTER_API_KEY),
        # None = Pages not configured (UI hides the publish button); {} = configured, never published
        "publish": (publish.last_status() or {}) if publish.configured() else None,
        "source_problems": source_problems(c),
        "posts_this_week": c.execute("SELECT COUNT(*) FROM posts WHERE posted_at >= ?", (week_ago,)).fetchone()[0],
        "metrics_due": sum(1 for p in posts.list_posts(c) if p["due"]),
    }


def source_problems(c) -> list[dict]:
    """Venue calendars whose latest read failed or found nothing, so a broken reader doesn't fail silently."""
    names = {v["key"]: v["name"] for v in config.VENUE_CALENDARS}
    rows = c.execute("""SELECT s.* FROM source_runs s
                        WHERE s.run_id = (SELECT MAX(run_id) FROM source_runs s2 WHERE s2.source = s.source)""")
    return [{"source": names.get(r["source"], r["source"]), "error": r["error"] or "0 shows found"}
            for r in rows if r["source"] in names and (not r["ok"] or not r["count"])]


@app.get("/api/alerts")
def list_alerts(status: str = "open", limit: int = 100, c=Depends(get_db)):
    where = {"open": "a.status != 'dismissed'", "dismissed": "a.status = 'dismissed'", "all": "1=1"}.get(status)
    if where is None:
        raise HTTPException(400, "status must be open, dismissed or all")
    alerts = [dict(r) for r in c.execute(
        f"SELECT * FROM alerts a WHERE {where} ORDER BY a.created_at DESC, a.score DESC LIMIT ?", (limit,))]
    posted = posts.posts_by_alert(c)
    for a in alerts:
        a["facts"] = json.loads(a["facts"])
        a["posts"] = posted.get(a["id"], [])
        a["angles"] = angles.angles_for(a["facts"])
        a["context"] = context.cached(c, context.subject(a["facts"]))
        a["context_supported"] = context.subject(a["facts"]) is not None
        a["drafts"] = [drafts.draft_row(d, a["facts"]) for d in c.execute(
            "SELECT * FROM drafts WHERE alert_id=? ORDER BY id DESC", (a["id"],))]
    return alerts


class AlertUpdate(BaseModel):
    status: str | None = None
    shortlisted: bool | None = None
    snoozed_until: str | None = None  # UTC ISO; send null to un-snooze
    note: str | None = None
    planned_for: str | None = None    # ISO with timezone; null takes it off the calendar
    series: str | None = None         # null/blank = the format's default series


@app.patch("/api/alerts/{alert_id}")
def update_alert(alert_id: int, body: AlertUpdate, c=Depends(get_db)):
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in ("new", "drafted", "dismissed"):
        raise HTTPException(400, "bad status")
    for k in ("snoozed_until", "planned_for"):
        if fields.get(k):
            fields[k] = _post_call(posts._utc, fields[k])
    if "series" in fields:
        fields["series"] = (fields["series"] or "").strip()[:60] or None
    if "shortlisted" in fields:
        fields["shortlisted"] = int(bool(fields["shortlisted"]))
    if not c.execute("SELECT 1 FROM alerts WHERE id=?", (alert_id,)).fetchone():
        raise HTTPException(404)
    if fields:
        c.execute(f"UPDATE alerts SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), alert_id])
        c.commit()
    return {"ok": True}


@app.get("/api/queue")
def queue(c=Depends(get_db)):
    """Video ideas ranked by the experimental opportunity score (tracker/opportunities.py)."""
    items = opportunities.queue(c)
    now = datetime.now(timezone.utc)
    soon = (now + timedelta(hours=48)).isoformat()
    active = [i for i in items if i["stage"] in opportunities.ACTIVE_STAGES]
    return {
        "items": items,
        "counts": {
            "new": sum(i["stage"] in ("new", "shortlisted") for i in active),
            "time_sensitive": sum(bool(i["post_by"]) and i["post_by"] <= soon for i in active),
            "to_review": sum(i["stage"] in ("review", "needs_refresh") for i in active),
            "ready_to_film": sum(i["stage"] == "approved" for i in active),
            "needs_refresh": sum(i["stage"] == "needs_refresh" for i in active),
        },
        "weights": config.OPPORTUNITY_WEIGHTS,
        "rules_version": config.OPPORTUNITY_RULES_VERSION,
    }


@app.get("/api/calendar")
def calendar(start: str | None = None, c=Depends(get_db)):
    """This week and next: planned, posted and due videos, with balance warnings (tracker/planner.py)."""
    try:
        first = date.fromisoformat(start) if start else None
    except ValueError:
        raise HTTPException(400, "start must be YYYY-MM-DD")
    return planner.calendar(c, start=first)


@app.get("/api/alerts/{alert_id}/slides")
def get_slides(alert_id: int, c=Depends(get_db)):
    """The idea's 9:16 slide manifest (built from facts, plus any saved edits)."""
    found = slides.deck(c, alert_id)
    if found is None:
        raise HTTPException(404)
    return found


class SlidesSave(BaseModel):
    overrides: dict


@app.put("/api/alerts/{alert_id}/slides")
def save_slides(alert_id: int, body: SlidesSave, c=Depends(get_db)):
    try:
        return slides.save(c, alert_id, body.overrides)
    except KeyError:
        raise HTTPException(404)
    except slides.SlidesError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/alerts/{alert_id}/slides/rebuild")
def rebuild_slides(alert_id: int, c=Depends(get_db)):
    """Rebuild from the current facts, keeping edits whose underlying fact didn't change."""
    try:
        return slides.rebuild(c, alert_id)
    except KeyError:
        raise HTTPException(404)


@app.delete("/api/alerts/{alert_id}/slides")
def reset_slides(alert_id: int, c=Depends(get_db)):
    found = slides.reset(c, alert_id)
    if found is None:
        raise HTTPException(404)
    return found


@app.post("/api/alerts/{alert_id}/context")
def web_context(alert_id: int, refresh: bool = False, c=Depends(get_db)):
    """Web context for one idea (a few seconds; reuses a fresh lookup for free)."""
    try:
        found = context.lookup(c, alert_id, refresh=refresh)
    except context.ContextError as exc:
        raise HTTPException(exc.status, str(exc))
    return {"context": found, "usage": context.usage(c)}


@app.get("/api/context/usage")
def web_context_usage(c=Depends(get_db)):
    return context.usage(c)


@app.post("/api/alerts/{alert_id}/draft", status_code=202)
def regenerate(alert_id: int, angle: str | None = None, c=Depends(get_db)):
    row = c.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    facts = json.loads(row["facts"])
    if facts.get("format") == "music_story":
        raise HTTPException(400, "Drafting music stories comes with roadmap M6")
    if angle and not angles.find(facts, angle):
        raise HTTPException(400, f"no angle {angle!r} for this alert")

    use_llm = drafts.needs_llm(facts, angle)
    provider = "claude" if llm.uses_claude() else "ollama"
    cold = use_llm and provider == "ollama" and not llm.ollama_up()
    retried = []

    def progress(step: str, extra_generation: bool = False):
        _job["step"] = step
        if extra_generation:  # the fact-check retry: one more generation
            retried.append(step)
            _job["estimate"] = (_job["estimate"] or 0) + _gen_seconds[provider]

    def work():
        with closing(conn()) as job_conn:
            if use_llm:
                with llm.model_session():
                    progress("Writing the draft")
                    started = time.monotonic()
                    drafts.draft_alert(job_conn, alert_id, angle=angle, progress=progress)
                    if not retried:  # remember a clean single generation's time for the next estimate
                        _gen_seconds[provider] = min(max(time.monotonic() - started, 2.0), 300.0)
            else:  # templated format + chosen angle: no model, done in a moment
                drafts.draft_alert(job_conn, alert_id, angle=angle, progress=progress)
    estimate = (OLLAMA_START_SECONDS if cold else 0) + _gen_seconds[provider] if use_llm else 2
    return _start_job(f"draft for alert {alert_id}" + (f" ({angle} angle)" if angle else ""), work,
                      alert_id=alert_id, step="Starting the model" if cold else "Writing the draft",
                      estimate=round(estimate))


class DraftUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    hook: str | None = None
    script: str | None = None
    caption: str | None = None
    hashtags: str | None = None


@app.patch("/api/drafts/{draft_id}")
def update_draft(draft_id: int, body: DraftUpdate, c=Depends(get_db)):
    fields = body.model_dump(exclude_none=True)
    if "status" in fields and fields["status"] not in ("pending", "approved", "filmed", "rejected"):
        raise HTTPException(400, "bad status")
    if not fields:
        return {"ok": True}
    sets = ", ".join(f"{k}=?" for k in fields)
    if not c.execute(f"UPDATE drafts SET {sets} WHERE id=?", [*fields.values(), draft_id]).rowcount:
        raise HTTPException(404)
    c.commit()
    return {"ok": True}


# ---------- posted videos + their numbers (all typed in by you; see tracker/posts.py) ----------

class PostFields(BaseModel):
    url: str | None = None
    title: str | None = None
    posted_at: str | None = None    # ISO with timezone; the browser sends UTC
    format: str | None = None
    video_style: str | None = None
    hook: str | None = None
    length_seconds: int | None = None
    notes: str | None = None
    series: str | None = None


class PostCreate(PostFields):
    alert_id: int | None = None
    draft_id: int | None = None


class MetricsCreate(BaseModel):
    observed_at: str | None = None  # default: now
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    followers_gained: int | None = None
    avg_watch_seconds: float | None = None
    completion_rate: float | None = None
    notes: str | None = None


def _post_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except posts.PostError as exc:
        raise HTTPException(400, str(exc))
    except LookupError:
        raise HTTPException(404)


@app.get("/api/posts")
def list_posts(c=Depends(get_db)):
    return posts.list_posts(c)


@app.post("/api/posts", status_code=201)
def create_post(body: PostCreate, c=Depends(get_db)):
    return {"id": _post_call(posts.create_post, c, **body.model_dump(exclude_none=True))}


@app.patch("/api/posts/{post_id}")
def update_post(post_id: int, body: PostFields, c=Depends(get_db)):
    _post_call(posts.update_post, c, post_id, **body.model_dump(exclude_unset=True))  # explicit null clears a field
    return {"ok": True}


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: int, c=Depends(get_db)):
    _post_call(posts.delete_post, c, post_id)
    return {"ok": True}


@app.post("/api/posts/{post_id}/metrics", status_code=201)
def add_metrics(post_id: int, body: MetricsCreate, c=Depends(get_db)):
    return {"id": _post_call(posts.add_metrics, c, post_id, **body.model_dump(exclude_none=True))}


@app.delete("/api/metrics/{metrics_id}")
def delete_metrics(metrics_id: int, c=Depends(get_db)):
    _post_call(posts.delete_metrics, c, metrics_id)
    return {"ok": True}


@app.get("/api/analytics")
def analytics(group_by: str = "format", c=Depends(get_db)):
    return _post_call(posts.analytics, c, group_by)


class TrendFields(BaseModel):
    kind: str | None = None
    text: str | None = None
    terms: list[str] | None = None
    scope: str | None = None
    confidence: str | None = None
    momentum: str | None = None
    source_url: str | None = None
    notes: str | None = None
    observed_at: str | None = None  # ISO with timezone; default now
    expires_at: str | None = None   # default observed_at + TREND_NOTE_DAYS


def _trend_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except trends.TrendError as exc:
        raise HTTPException(400, str(exc))
    except LookupError:
        raise HTTPException(404)


@app.get("/api/trends")
def list_trends(c=Depends(get_db)):
    return trends.list_notes(c)


@app.post("/api/trends", status_code=201)
def create_trend(body: TrendFields, c=Depends(get_db)):
    return {"id": _trend_call(trends.create_note, c, **body.model_dump(exclude_none=True))}


@app.patch("/api/trends/{note_id}")
def update_trend(note_id: int, body: TrendFields, c=Depends(get_db)):
    _trend_call(trends.update_note, c, note_id, **body.model_dump(exclude_unset=True))
    return {"ok": True}


@app.delete("/api/trends/{note_id}")
def delete_trend(note_id: int, c=Depends(get_db)):
    _trend_call(trends.delete_note, c, note_id)
    return {"ok": True}


@app.get("/api/events")
def list_events(q: str = "", genre: str = "", days: int = 60, limit: int = 300, c=Depends(get_db)):
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    end = (today.fromordinal(today.toordinal() + days)).isoformat()
    rows = [e for e in upcoming_events(c, today.isoformat()) if e["local_date"] <= end]
    if q:
        ql = q.lower()
        rows = [e for e in rows if ql in (e["name"] or "").lower() or ql in (e["headliner"] or "").lower()
                or ql in (e["venue_name"] or "").lower()]
    if genre:
        rows = [e for e in rows if e["genre"] == genre]
    rows.sort(key=lambda e: (e["local_date"], e["local_time"] or ""))
    keep = ["id", "name", "headliner", "support", "local_date", "local_time", "status", "venue_name", "genre",
            "subgenre", "price_min", "price_max", "onsale_start", "url", "image_url", "score", "popularity", "fans", "my_plays", "niche",
            "first_seen", "genre_source", "ticketmaster_genre"]
    return [{k: e.get(k) for k in keep} for e in rows[:limit]]


@app.get("/api/genres")
def genres(c=Depends(get_db)):
    """Genres of upcoming events, most common first: the same genres the events list filters on,
    including ones filled in from the artist (genres.py)."""
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    counts = Counter(e["genre"] for e in upcoming_events(c, today) if e["genre"])
    return [g for g, _ in counts.most_common()]


@app.get("/api/runs")
def runs(limit: int = 20, source: str | None = None, c=Depends(get_db)):
    """Pipeline runs. The 30-minute news checks are left out unless asked for (source=news): the Music news tab
    shows them, and 48 a day would bury the pipeline's own runs."""
    where, args = ("source = ?", (source,)) if source else ("source != 'news'", ())
    return [dict(r) for r in c.execute(f"SELECT * FROM runs WHERE {where} ORDER BY id DESC LIMIT ?", (*args, limit))]


@app.get("/api/news")
def news_articles(days: int = 3, source: str | None = None, mine: bool = False, limit: int = 500, c=Depends(get_db)):
    """Recent music-news articles, newest first (tracker/news.py), with the watchlist artists they mention
    (tracker/watchlist.py). `mine`: only those. `seen_at` is the feed's date, or when we first read it if the feed
    has none."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    rows = c.execute("""SELECT a.id, a.url, a.title, a.excerpt, a.published_at, a.fetched_at, a.categories,
                               a.first_read, s.key AS source_key, s.name AS source_name,
                               COALESCE(a.published_at, a.fetched_at) AS seen_at
                        FROM articles a JOIN news_sources s ON s.id = a.source_id
                        WHERE COALESCE(a.published_at, a.fetched_at) >= ? AND (? IS NULL OR s.key = ?)
                          AND (? = 0 OR EXISTS (SELECT 1 FROM article_artists aa WHERE aa.article_id = a.id))
                        -- undated items from a source's first read could be any age: after the dated ones
                        ORDER BY (a.published_at IS NULL AND a.first_read), seen_at DESC, a.id DESC
                        LIMIT ?""", (since, source, source, int(mine), limit)).fetchall()
    taste = listening.load()
    tags: dict[int, list] = {}
    ids = [r["id"] for r in rows]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for t in c.execute(f"""SELECT aa.article_id, aa.match, aa.location, aa.confidence, w.id, w.name, w.key, w.tier
                               FROM article_artists aa JOIN artist_identities w ON w.id = aa.artist_id
                               WHERE aa.article_id IN ({','.join('?' * len(chunk))})""", chunk):
            tags.setdefault(t["article_id"], []).append(
                {"id": t["id"], "name": t["name"], "tier": t["tier"], "match": t["match"], "location": t["location"],
                 "confidence": t["confidence"], "plays": taste.plays.get(t["key"], 0)})
    return [{**dict(r), "categories": json.loads(r["categories"] or "[]"), "first_read": bool(r["first_read"]),
             "artists": sorted(tags.get(r["id"], []), key=lambda t: -t["plays"])}
            for r in rows]


@app.get("/api/news/stories")
def news_stories(days: int = 7, origin: str | None = None, sort: str = "score", c=Depends(get_db)):
    """Watchlist stories (tracker/stories.py), each with its sources, evidence and music score (tracker/music.py).
    `origin`: news (from articles) or catalog (releases no feed covered, tracker/catalog.py). `sort`: score (For you:
    unhandled stories first, best first) or recent (newest activity first)."""
    if origin not in (None, "news", "catalog"):
        raise HTTPException(400, "origin must be news or catalog")
    if sort not in ("score", "recent"):
        raise HTTPException(400, "sort must be score or recent")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    # Stories made only of undated items from before tracking began have an unknown time: after the dated ones
    rows = c.execute("""SELECT * FROM stories WHERE last_seen >= ? AND (? IS NULL OR origin = ?)
                        ORDER BY dated DESC, last_seen DESC, id DESC""", (since, origin, origin)).fetchall()
    if not rows:
        return []
    ctx = music.context(c)
    artists = {i: {k: a[k] for k in ("id", "name", "tier", "plays", "seen_live")} for i, a in ctx["artists"].items()}
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    sources: dict[int, list] = {}
    for a in c.execute(f"""SELECT x.story_id, a.id, a.title, a.url, s.name AS source_name,
                                  COALESCE(a.published_at, a.fetched_at) AS seen_at
                           FROM story_articles x JOIN articles a ON a.id = x.article_id
                           JOIN news_sources s ON s.id = a.source_id
                           WHERE x.story_id IN ({marks}) ORDER BY seen_at""", ids):
        sources.setdefault(a["story_id"], []).append({k: a[k] for k in ("id", "title", "url", "source_name", "seen_at")})
    evidence: dict[int, list] = {}
    for e in c.execute(f"SELECT * FROM story_evidence WHERE story_id IN ({marks}) ORDER BY observed_at", ids):
        evidence.setdefault(e["story_id"], []).append(
            {k: e[k] for k in ("kind", "provider", "url", "claim", "value", "excerpt", "observed_at")})
    out = []
    for r in rows:
        d = dict(r)
        d["primary_artist"] = artists.get(r["primary_artist_id"])
        d["other_artists"] = [artists[i] for i in json.loads(r["other_artist_ids"] or "[]") if i in artists]
        d["sources"] = sources.get(r["id"], [])
        d["source_count"] = len({x["source_name"] for x in d["sources"]})
        d["sensitive"], d["dated"] = bool(d["sensitive"]), bool(d["dated"])
        d["evidence"] = evidence.get(r["id"], [])
        d.update(music.evaluate(d, ctx))
        out.append(d)
    if sort == "score":
        out.sort(key=music.rank_key)
    return out


class StoryFeedback(BaseModel):
    feedback: str | None  # interested, not_my_lane, already_covered; null clears it


@app.post("/api/news/stories/{story_id}/feedback")
def story_feedback(story_id: int, body: StoryFeedback, c=Depends(get_db)):
    try:
        music.set_feedback(c, story_id, body.feedback)
    except KeyError:
        raise HTTPException(404)
    except music.MusicError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "feedback": body.feedback}


@app.post("/api/news/stories/{story_id}/send", status_code=201)
def story_send(story_id: int, c=Depends(get_db)):
    """Send to queue: a music_story idea built from the story's evidence (nothing posts by itself)."""
    try:
        return {"alert_id": music.send(c, story_id)}
    except KeyError:
        raise HTTPException(404)


@app.get("/api/watchlist")
def get_watchlist(c=Depends(get_db)):
    """Watchlist artists with live play counts (never stored) and articles about them in the last 30 days."""
    return watchlist.listing(c)


class WatchlistAdd(BaseModel):
    name: str
    tier: str = "watch"
    aliases: list[str] = []


@app.post("/api/watchlist", status_code=201)
def add_watchlist(body: WatchlistAdd, c=Depends(get_db)):
    try:
        return {"id": watchlist.add(c, body.name, body.tier, body.aliases)}
    except watchlist.WatchlistError as exc:
        raise HTTPException(400, str(exc))


@app.patch("/api/watchlist/{artist_id}")
def patch_watchlist(artist_id: int, body: dict, c=Depends(get_db)):
    allowed = {"tier", "muted", "aliases", "common_word_override", "seen_live"}
    if not body or set(body) - allowed:
        raise HTTPException(400, f"can change: {', '.join(sorted(allowed))}")
    try:
        watchlist.update(c, artist_id, body)
        return next(r for r in watchlist.listing(c) if r["id"] == artist_id)  # same shape as GET /api/watchlist
    except KeyError:
        raise HTTPException(404, "no such artist")
    except watchlist.WatchlistError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/news/sources")
def news_sources(c=Depends(get_db)):
    """Feed health for the Sources panel, plus the latest news checks."""
    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat()
    rows = c.execute("""SELECT s.*, COUNT(a.id) AS articles,
                               SUM(COALESCE(a.published_at, a.fetched_at) >= ?) AS last_24h
                        FROM news_sources s LEFT JOIN articles a ON a.source_id = s.id
                        GROUP BY s.id ORDER BY s.enabled DESC, s.name""", (day_ago,))
    sources = [{k: r[k] for k in ("key", "name", "url", "enabled", "poll_minutes", "last_fetched_at", "last_ok_at",
                                  "last_not_modified_at", "last_error", "failures", "terms_note", "articles")}
               | {"enabled": bool(r["enabled"]), "niches": json.loads(r["niches"] or "[]"),
                  "last_24h": r["last_24h"] or 0}
               for r in rows]
    return {"sources": sources, "checks": runs(limit=10, source="news", c=c)}


@app.get("/api/remote-edits")
def remote_edits(c=Depends(get_db)):
    """Changes made from your phone through the tunnel (empty when phone editing is off)."""
    return {"enabled": remote.enabled(), "host": config.REMOTE_HOST or None, "edits": remote.recent(c)}


@app.post("/api/publish", status_code=202)
def trigger_publish():
    if not publish.configured():
        raise HTTPException(400, "Cloudflare Pages isn't configured (PAGES_PROJECT in .env)")

    def work():
        with closing(conn()) as job_conn:
            result = publish.publish(job_conn)
        if not result.startswith("published"):
            raise RuntimeError(result)
    return _start_job("publish snapshot", work)


@app.post("/api/run", status_code=202)
def trigger_run(drafts_too: bool = True):
    from . import run as pipeline  # local import: avoids a cycle at startup
    return _start_job("pipeline run", lambda: pipeline.run(make_drafts=drafts_too))


# ---------- static React app ----------
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    file = (DIST / path).resolve()
    if path and file.is_file() and file.is_relative_to(DIST.resolve()):  # favicon etc.; no ../ escapes
        return FileResponse(file)
    index = DIST / "index.html"
    if not index.exists():
        return {"message": "Dashboard not built yet. Run: cd dashboard && npm install && npm run build"}
    return FileResponse(index)


def main():
    import uvicorn
    from .run import setup_logging
    setup_logging()
    # log_config=None: use our handlers (logs/pipeline.log) instead of uvicorn's console setup
    uvicorn.run(app, host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
