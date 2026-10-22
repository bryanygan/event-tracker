"""Local dashboard API + static file server for the React app in dashboard/dist.

Start with:  .venv\\Scripts\\python -m tracker.api
Then open:   http://127.0.0.1:8765
Bound to 127.0.0.1 only, so nothing on your network can reach it.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from contextlib import closing

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, drafts, llm, publish
from .scoring import upcoming_events

log = logging.getLogger(__name__)
app = FastAPI(title="Philly Events Tracker")
DIST = config.ROOT / "dashboard" / "dist"

# One background job at a time (a pipeline run or a draft regeneration), so the
# N95 never runs two LLM generations at once.
_job_lock = threading.Lock()
_job = {"name": None, "started": None, "error": None, "finished": None}


def conn():
    return db.connect()


def get_db():
    """One connection per request, always closed (open SQLite handles lock the file on Windows)."""
    c = conn()
    try:
        yield c
    finally:
        c.close()


def _start_job(name: str, fn) -> dict:
    if not _job_lock.acquire(blocking=False):
        raise HTTPException(409, f"Busy: {_job['name']} is still running")
    _job.update(name=name, started=datetime.now().isoformat(timespec="seconds"), error=None, finished=None)

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
    return dict(_job)


@app.get("/api/job")
def job_status():
    return {**_job, "running": _job_lock.locked()}


@app.get("/api/summary")
def summary(c=Depends(get_db)):
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    last = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "upcoming_events": c.execute("SELECT COUNT(*) FROM events WHERE active=1 AND local_date>=?",
                                     (today,)).fetchone()[0],
        "open_alerts": c.execute("SELECT COUNT(*) FROM alerts WHERE status!='dismissed'").fetchone()[0],
        "pending_drafts": c.execute("SELECT COUNT(*) FROM drafts WHERE status='pending'").fetchone()[0],
        "last_run": dict(last) if last else None,
        "model": config.OLLAMA_MODEL,
        "has_tm_key": bool(config.TICKETMASTER_API_KEY),
        # None = Pages not configured (UI hides the publish button); {} = configured, never published
        "publish": (publish.last_status() or {}) if publish.configured() else None,
        "source_problems": source_problems(c),
    }


def source_problems(c) -> list[dict]:
    """Venue calendars whose latest read failed or found nothing, so a broken reader doesn't fail silently."""
    names = {v["key"]: v["name"] for v in config.VENUE_CALENDARS}
    rows = c.execute("""SELECT s.* FROM source_runs s
                        WHERE s.run_id = (SELECT MAX(run_id) FROM source_runs s2 WHERE s2.source = s.source)""")
    return [{"source": names.get(r["source"], r["source"]), "error": r["error"] or "0 shows found"}
            for r in rows if r["source"] in names and (not r["ok"] or not r["count"])]


def _draft_row(r) -> dict:
    d = dict(r)
    d["onscreen"] = json.loads(d["onscreen"] or "[]")
    d["warnings"] = json.loads(d["warnings"] or "[]")
    return d


@app.get("/api/alerts")
def list_alerts(status: str = "open", limit: int = 100, c=Depends(get_db)):
    where = {"open": "a.status != 'dismissed'", "dismissed": "a.status = 'dismissed'", "all": "1=1"}.get(status)
    if where is None:
        raise HTTPException(400, "status must be open, dismissed or all")
    alerts = [dict(r) for r in c.execute(
        f"SELECT * FROM alerts a WHERE {where} ORDER BY a.created_at DESC, a.score DESC LIMIT ?", (limit,))]
    for a in alerts:
        a["facts"] = json.loads(a["facts"])
        a["drafts"] = [_draft_row(d) for d in c.execute(
            "SELECT * FROM drafts WHERE alert_id=? ORDER BY id DESC", (a["id"],))]
    return alerts


class AlertUpdate(BaseModel):
    status: str


@app.patch("/api/alerts/{alert_id}")
def update_alert(alert_id: int, body: AlertUpdate, c=Depends(get_db)):
    if body.status not in ("new", "drafted", "dismissed"):
        raise HTTPException(400, "bad status")
    if not c.execute("UPDATE alerts SET status=? WHERE id=?", (body.status, alert_id)).rowcount:
        raise HTTPException(404)
    c.commit()
    return {"ok": True}


@app.post("/api/alerts/{alert_id}/draft", status_code=202)
def regenerate(alert_id: int, c=Depends(get_db)):
    if not c.execute("SELECT 1 FROM alerts WHERE id=?", (alert_id,)).fetchone():
        raise HTTPException(404)

    def work():
        with llm.ollama_session():
            with closing(conn()) as job_conn:
                drafts.draft_alert(job_conn, alert_id)
    return _start_job(f"draft for alert {alert_id}", work)


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
            "subgenre", "price_min", "price_max", "onsale_start", "url", "image_url", "score", "popularity", "fans",
            "first_seen"]
    return [{k: e.get(k) for k in keep} for e in rows[:limit]]


@app.get("/api/genres")
def genres(c=Depends(get_db)):
    return [r[0] for r in c.execute(
        "SELECT genre FROM events WHERE active=1 AND genre IS NOT NULL GROUP BY genre ORDER BY COUNT(*) DESC")]


@app.get("/api/runs")
def runs(limit: int = 20, c=Depends(get_db)):
    return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


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
