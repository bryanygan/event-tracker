"""Publish a public, read-only showcase of the dashboard (for a resume) to its own Cloudflare Pages project.

    .venv\\Scripts\\python -m tracker.showcase            # export + deploy (what the pipeline does)
    .venv\\Scripts\\python -m tracker.showcase --export   # just write data/showcase/ (no network)
    .venv\\Scripts\\python -m tracker.showcase --create   # one-time: create the Pages project

Same static build as the private snapshot (tracker/publish.py), but meant to be public, so the data is
cleaned first: no listening history (play counts, "seen live", watchlist), no private notes or trends, no
costs, no run errors, no link to the editable dashboard. Before uploading, `leaks` scans every file for
anything that must stay on this PC (.env values, the phone-editing host, local paths, play counts) and
refuses to upload if it finds one. Leave SHOWCASE_PROJECT empty to turn it off.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import api, config, db, posts, publish, slides, vinyl

log = logging.getLogger(__name__)

SITE_DIR = config.DATA_DIR / "showcase"
STATUS_FILE = config.DATA_DIR / "showcase_status.json"

# Keys dropped wherever they appear: listening history, private notes, costs
PRIVATE_KEYS = {"my_plays", "your_plays", "plays", "seen_live", "my_bucket", "tier", "notes", "cost_usd",
                "month_cost_usd", "drafting", "publish", "edit_url", "error", "metrics_due"}
# Play counts written into reason lines and titles ("in your rotation (412 plays)", ", 88 plays", "🎧 ")
_SCRUB = [(re.compile(r"\s*\(\d[\d,]* plays?\)"), ""), (re.compile(r",\s*\d[\d,]* plays?\b"), ""),
          (re.compile(r"🎧\s?\d*"), "")]
_LEAK_PATTERNS = [re.compile(r"\b\d[\d,]* plays?\b", re.I), re.compile(r"[A-Za-z]:\\\\?Users", re.I),
                  re.compile(r"plays\.db", re.I)]


# .env settings that aren't secret and may legitimately show (the model name on the header)
_PUBLIC_SETTINGS = {"OLLAMA_MODEL", "OLLAMA_URL", "DASHBOARD_PORT", "SHOWCASE_PROJECT", "SHOWCASE_REPO_URL"}


class ShowcaseError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.SHOWCASE_PROJECT)


def clean(value):
    """A copy with the private keys removed and play counts scrubbed out of every string."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        for pattern, repl in _SCRUB:
            value = pattern.sub(repl, value)
        return value
    return value


def _secrets() -> list[str]:
    """Values that must never be published: every .env value long enough to be a key, host or path."""
    found = [str(config.ROOT)]
    env = config.ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() not in _PUBLIC_SETTINGS and len(v) >= 6 and not v.isdigit():
                    found.append(v)
    if config.REMOTE_HOST:  # the phone-editing host and its domain
        found += [config.REMOTE_HOST, config.REMOTE_HOST.split(".", 1)[-1]]
    return found


def leaks(site_dir: Path) -> list[str]:
    """What in the exported files must not go public (a description, never the secret itself)."""
    secrets = _secrets()
    problems = []
    for f in sorted((site_dir / "data").glob("*.json")):
        text = f.read_text(encoding="utf-8")
        if any(s in text for s in secrets):
            problems.append(f"{f.name}: contains a value from .env")
        for p in _LEAK_PATTERNS:
            m = p.search(text)
            if m:
                problems.append(f"{f.name}: matches {p.pattern!r} near {text[max(0, m.start() - 40):m.end() + 10]!r}")
    return problems


def _runs(conn) -> list[dict]:
    return [{k: r[k] for k in ("id", "started_at", "finished_at", "source", "events_fetched", "api_calls", "ok")}
            for r in api.runs(limit=20, c=conn)]


def export(conn, out_dir: Path = SITE_DIR) -> Path:
    """Write the showcase: the read-only React build plus cleaned JSON."""
    if not (publish.STATIC_BUILD / "index.html").exists():
        raise ShowcaseError("Static dashboard not built. Run: cd dashboard && npm run build:static")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(publish.STATIC_BUILD, out_dir)
    data = out_dir / "data"
    data.mkdir()

    summary = api.summary(c=conn)
    summary.update(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), showcase=True,
                   repo_url=config.SHOWCASE_REPO_URL or None, source_problems=[])
    last = summary.get("last_run")
    summary["last_run"] = next((r for r in _runs(conn) if last and r["id"] == last["id"]), None)
    alerts = api.list_alerts(status="open", limit=200, c=conn)
    vinyl_ideas = api.list_alerts(status="open", limit=100, kind="vinyl", c=conn)
    news_sources = api.news_sources(c=conn)
    files = {
        "summary": summary,
        "alerts": alerts + vinyl_ideas,
        "events": api.list_events(q="", genre="", days=config.LOOKAHEAD_DAYS, limit=5000, c=conn),
        "genres": api.genres(c=conn),
        "runs": _runs(conn),
        "posts": api.list_posts(c=conn),
        "queue": api.queue(c=conn),
        "calendar": api.calendar(c=conn),
        "analytics": {g: api.analytics(group_by=g, c=conn) for g in posts.GROUP_BY},
        "trends": [],  # your own notes
        "vinyl": vinyl.overview(conn),
        "news": api.news_articles(days=14, c=conn),
        "news_sources": {"sources": news_sources["sources"],
                         "checks": [{k: r[k] for k in ("id", "started_at", "finished_at", "events_fetched", "ok")}
                                    for r in news_sources["checks"]]},
        "stories": api.news_stories(days=14, c=conn),
    }
    files["slides"] = slides.export_all(conn, [a["id"] for a in alerts])
    for name, payload in files.items():
        (data / f"{name}.json").write_text(json.dumps(clean(payload), ensure_ascii=False), encoding="utf-8")
    (out_dir / "_headers").write_text(publish.HEADERS_FILE, encoding="utf-8")
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    log.info("Exported showcase to %s (%d ideas, %d events)", out_dir, len(files["alerts"]), len(files["events"]))
    return out_dir


def create_project() -> str:
    """Create the Pages project through the API (wrangler's create silently does nothing in CI mode)."""
    name = config.SHOWCASE_PROJECT
    try:
        return f"already exists: {publish.cf_api('GET', f'/pages/projects/{name}')['subdomain']}"
    except publish.PublishError:
        pass
    result = publish.cf_api("POST", "/pages/projects", json={"name": name, "production_branch": "main"})
    return f"created: {result['subdomain']}"


def deploy(site_dir: Path = SITE_DIR) -> str:
    return publish.run_wrangler("pages", "deploy", str(site_dir), "--project-name", config.SHOWCASE_PROJECT,
                                "--branch", "main", "--commit-dirty=true")


def _save_status(ok: bool, message: str):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": ok,
                                       "message": message}), encoding="utf-8")


def publish_showcase(conn) -> str:
    """Export, check for leaks, deploy. Never raises; returns a status line."""
    if not configured():
        return "skipped (SHOWCASE_PROJECT not set)"
    try:
        site = export(conn)
        problems = leaks(site)
        if problems:
            raise ShowcaseError("refusing to upload, private data in the export: " + "; ".join(problems[:5]))
        deploy(site)
        msg = "showcase published"
        _save_status(True, msg)
    except Exception as exc:
        msg = f"showcase failed: {exc}"
        _save_status(False, str(exc)[:300])
        log.warning(msg)
    return msg


def main(argv=None):
    from .run import setup_logging
    p = argparse.ArgumentParser(description="Publish the public read-only showcase to Cloudflare Pages")
    p.add_argument("--export", action="store_true", help="only write data/showcase/ and check it, don't deploy")
    p.add_argument("--create", action="store_true", help="one-time: create the Pages project")
    args = p.parse_args(argv)
    setup_logging()
    if args.create:
        print(create_project())
        return
    with closing(db.connect()) as conn:
        if args.export:
            site = export(conn)
            print(site)
            print("\n".join(leaks(site)) or "no leaks found")
        else:
            print(publish_showcase(conn))


if __name__ == "__main__":
    main()
