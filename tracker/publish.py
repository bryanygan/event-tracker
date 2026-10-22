"""Publish a read-only snapshot of the dashboard to Cloudflare Pages.

    .venv\\Scripts\\python -m tracker.publish            # export + deploy (what the pipeline does)
    .venv\\Scripts\\python -m tracker.publish --export   # just write data/site/ (no network)
    .venv\\Scripts\\python -m tracker.publish --check    # is the site behind Cloudflare Access?
    .venv\\Scripts\\python -m tracker.publish --setup-access "Admin Only"  # one-time: lock the site

How it works: the React app is built once in static mode (`npm run build:static` → dashboard/dist-static),
which reads JSON files instead of calling the API. After each pipeline run we copy that build into
data/site/, write the JSON (same data the local API serves), and upload it with `wrangler pages deploy`.

Privacy guardrail: drafts are private, so before every deploy we check that the site answers with a
redirect to Cloudflare Access. If it doesn't, we refuse to upload (set PAGES_ALLOW_PUBLIC=1 to override).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import api, config, db

log = logging.getLogger(__name__)

STATIC_BUILD = config.ROOT / "dashboard" / "dist-static"
SITE_DIR = config.DATA_DIR / "site"
STATUS_FILE = config.DATA_DIR / "publish_status.json"

HEADERS_FILE = """/*
  X-Robots-Tag: noindex, nofollow
  Referrer-Policy: no-referrer
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
/data/*
  Cache-Control: no-store
"""


class PublishError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.PAGES_PROJECT)


CF_API = "https://api.cloudflare.com/client/v4"
_subdomain_cache: dict[str, str] = {}


def cf_api(method: str, path: str, **kw) -> dict:
    """Call the Cloudflare REST API with the token from .env. Returns `result`."""
    r = requests.request(method, f"{CF_API}/accounts/{config.CLOUDFLARE_ACCOUNT_ID}{path}", timeout=30,
                         headers={"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}, **kw)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or not body.get("success", False):
        raise PublishError(f"Cloudflare API {method} {path}: HTTP {r.status_code} {body.get('errors') or r.text[:200]}")
    return body["result"]


def site_url() -> str:
    """The project's real address. Cloudflare appends a random suffix when <name>.pages.dev is taken
    (e.g. <name>-2h7.pages.dev), so ask the API instead of assuming."""
    name = config.PAGES_PROJECT
    if name not in _subdomain_cache:
        try:
            _subdomain_cache[name] = cf_api("GET", f"/pages/projects/{name}")["subdomain"]
        except Exception as exc:
            log.warning("Couldn't look up Pages subdomain (%s); assuming %s.pages.dev", exc, name)
            return f"https://{name}.pages.dev/"
    return f"https://{_subdomain_cache[name]}/"


# ---------- export ----------

def export(conn, out_dir: Path = SITE_DIR) -> Path:
    """Write the static site: the read-only React build plus JSON snapshots of the data."""
    if not (STATIC_BUILD / "index.html").exists():
        raise PublishError("Static dashboard not built. Run: cd dashboard && npm run build:static")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(STATIC_BUILD, out_dir)
    data = out_dir / "data"
    data.mkdir()

    summary = api.summary(c=conn)
    summary["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary.pop("publish", None)
    files = {
        "summary": summary,
        "alerts": api.list_alerts(status="open", limit=200, c=conn),
        "events": api.list_events(q="", genre="", days=config.LOOKAHEAD_DAYS, limit=5000, c=conn),
        "genres": api.genres(c=conn),
        "runs": api.runs(limit=20, c=conn),
        "posts": api.list_posts(c=conn),
        "analytics": {g: api.analytics(group_by=g, c=conn) for g in ("format", "video_style")},
    }
    for name, payload in files.items():
        (data / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (out_dir / "_headers").write_text(HEADERS_FILE, encoding="utf-8")
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    log.info("Exported snapshot to %s (%d alerts, %d events)", out_dir, len(files["alerts"]), len(files["events"]))
    return out_dir


# ---------- access check ----------

def access_protected(url: str | None = None) -> tuple[bool, str]:
    """True if the site redirects anonymous visitors to a Cloudflare Access login."""
    url = url or site_url()
    try:
        r = requests.get(url, allow_redirects=False, timeout=15)
    except requests.RequestException as exc:
        return False, f"could not reach {url}: {exc}"
    location = r.headers.get("Location", "")
    if r.status_code in (301, 302, 303, 307) and "cloudflareaccess.com" in location:
        return True, "protected by Cloudflare Access"
    return False, f"{url} answered HTTP {r.status_code} without an Access login (not protected)"


def setup_access(policy_name: str) -> str:
    """Create (once) the Cloudflare Access app that locks the site, using an existing reusable policy,
    so whoever that policy allows can log in. Covers the main URL and preview deployments."""
    host = site_url().removeprefix("https://").rstrip("/")
    existing = [a for a in cf_api("GET", "/access/apps") if host in (a.get("self_hosted_domains") or [a.get("domain")])]
    if existing:
        return f"Access app already exists: {existing[0]['name']}"
    policies = cf_api("GET", "/access/policies")  # reusable policies
    match = [p for p in policies if p["name"] == policy_name]
    if not match:
        raise PublishError(f"No reusable Access policy named {policy_name!r}; have: {[p['name'] for p in policies]}")
    app = cf_api("POST", "/access/apps", json={
        "name": "Philly Events dashboard",
        "type": "self_hosted",
        "domain": host,
        "self_hosted_domains": [host, f"*.{host}"],  # *. = preview deployments
        "session_duration": "720h",
        "app_launcher_visible": False,
        "policies": [{"id": match[0]["id"], "precedence": 1}],
    })
    return f"Created Access app {app['name']} for {host} with policy {policy_name!r}"


# ---------- deploy ----------

def _wrangler() -> str:
    exe = config.ROOT / "dashboard" / "node_modules" / ".bin" / ("wrangler.cmd" if os.name == "nt" else "wrangler")
    if not exe.exists():
        raise PublishError("wrangler not installed. Run: cd dashboard && npm install")
    return str(exe)


def run_wrangler(*args: str, timeout: int = 300) -> str:
    if not (config.CLOUDFLARE_API_TOKEN and config.CLOUDFLARE_ACCOUNT_ID):
        raise PublishError("CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID missing from .env")
    env = {**os.environ, "CLOUDFLARE_API_TOKEN": config.CLOUDFLARE_API_TOKEN,
           "CLOUDFLARE_ACCOUNT_ID": config.CLOUDFLARE_ACCOUNT_ID, "WRANGLER_SEND_METRICS": "false",
           "CI": "1"}  # CI=1: never prompt interactively
    proc = subprocess.run([_wrangler(), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout, cwd=config.ROOT,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise PublishError(f"wrangler {' '.join(args[:2])} failed: {output[-500:]}")
    return output


def deploy(site_dir: Path = SITE_DIR) -> str:
    out = run_wrangler("pages", "deploy", str(site_dir), "--project-name", config.PAGES_PROJECT,
                       "--branch", "main", "--commit-dirty=true")
    log.info("wrangler: %s", out.splitlines()[-1] if out else "(no output)")
    return out


def _save_status(ok: bool, message: str):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": ok,
                                       "message": message, "url": site_url() if configured() else None}),
                           encoding="utf-8")


def last_status() -> dict | None:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def publish(conn) -> str:
    """Export + deploy if Pages is configured and the site is private. Never raises; returns a status line."""
    if not configured():
        return "skipped (PAGES_PROJECT not set)"
    try:
        # Check the main URL *and* a made-up subdomain: every deploy also gets its own
        # <hash>.<project>.pages.dev URL serving the same files, which the *. Access rule must cover.
        protected, detail = access_protected()
        if protected:
            host = site_url().removeprefix("https://")
            protected, detail = access_protected(f"https://guardcheck.{host}")
            detail = f"preview URLs: {detail}"
        if not protected and not config.PAGES_ALLOW_PUBLIC:
            raise PublishError(f"refusing to upload: {detail}. Set up Cloudflare Access first "
                               "(or PAGES_ALLOW_PUBLIC=1 to publish anyway)")
        export(conn)
        deploy()
        msg = f"published to {site_url()}"
        _save_status(True, "published")
    except Exception as exc:
        msg = f"publish failed: {exc}"
        _save_status(False, str(exc)[:300])
        log.warning(msg)
    return msg


def main(argv=None):
    from .run import setup_logging
    p = argparse.ArgumentParser(description="Publish read-only snapshot to Cloudflare Pages")
    p.add_argument("--export", action="store_true", help="only write data/site/, don't deploy")
    p.add_argument("--check", action="store_true", help="only check whether the site is behind Access")
    p.add_argument("--setup-access", metavar="POLICY", help="lock the site with this reusable Access policy")
    args = p.parse_args(argv)
    setup_logging()
    if args.setup_access:
        print(setup_access(args.setup_access))
        return
    if args.check:
        ok, detail = access_protected()
        print(detail)
        sys.exit(0 if ok else 1)
    with closing(db.connect()) as conn:
        if args.export:
            print(export(conn))
        else:
            print(publish(conn))


if __name__ == "__main__":
    main()
