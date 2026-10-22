"""Editing from your phone: the live dashboard through a Cloudflare Tunnel, locked by Cloudflare Access.

How it fits together:
  phone → https://REMOTE_HOST → Cloudflare Access (login) → your existing cloudflared tunnel → 127.0.0.1:8765

The API still only listens on 127.0.0.1; nothing on your network or the internet reaches it directly. Requests
from the tunnel arrive from cloudflared on this PC, so we can't tell them apart by IP. Instead, any request whose
Host is REMOTE_HOST must carry the `Cf-Access-Jwt-Assertion` header Cloudflare adds after a login, and that token
must be signed by your Zero Trust team's keys and issued for this Access app (its audience tag). So a tunnel
misconfiguration (hostname without an Access app) or a local page sending a fake Host still gets a 403.

Every write made this way is logged in `remote_edits` (who, what, when, result), shown on the Runs tab.

One-time setup (creates the Access app with an existing reusable policy and prints the .env lines):
    .venv\\Scripts\\python -m tracker.remote --setup events.example.com "Admin Only"
Then add the public hostname to your tunnel in the Zero Trust dashboard (service: http://127.0.0.1:8765).
"""
from __future__ import annotations

import argparse
import logging
import threading
import time

import jwt
import requests

from . import config
from .store import now_iso

log = logging.getLogger(__name__)

HEADER = "cf-access-jwt-assertion"
KEY_REFRESH_SECONDS = 3600   # Cloudflare rotates the signing keys every few weeks and publishes the next one early
MAX_KEY_FETCHES_PER_HOUR = 6  # an unknown key id refetches, but garbage tokens can't make us hammer Cloudflare


class RemoteAuthError(Exception):
    pass


def enabled() -> bool:
    return bool(config.REMOTE_HOST and config.CF_ACCESS_TEAM_DOMAIN and config.CF_ACCESS_AUD)


# The team's public signing keys by key id, fetched from Cloudflare and kept for an hour.
_keys_lock = threading.Lock()
_keys: dict[str, object] = {}
_keys_at = 0.0
_fetch_times: list[float] = []


def _fetch_keys() -> None:
    global _keys, _keys_at
    now = time.monotonic()
    _fetch_times[:] = [t for t in _fetch_times if now - t < 3600]
    if len(_fetch_times) >= MAX_KEY_FETCHES_PER_HOUR:
        return  # keep what we have; an unknown key id then fails below
    _fetch_times.append(now)
    try:
        r = requests.get(f"{config.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs", timeout=10)
        r.raise_for_status()
        keys = jwt.PyJWKSet.from_dict(r.json())
    except (requests.RequestException, ValueError, jwt.PyJWTError) as exc:
        log.warning("Couldn't fetch Cloudflare Access keys: %s", exc)
        return
    _keys = {k.key_id: k.key for k in keys.keys if k.key_id}
    _keys_at = now


def _signing_key(token: str):
    """The team key that signed this token. An unknown key id (Cloudflare rotated keys) refetches the set, capped
    so a stream of made-up key ids can't turn into a stream of requests to Cloudflare."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise RemoteAuthError(f"bad token: {exc}") from None
    with _keys_lock:
        if kid not in _keys or time.monotonic() - _keys_at > KEY_REFRESH_SECONDS:
            _fetch_keys()
        if kid not in _keys:
            raise RemoteAuthError("token wasn't signed by your Cloudflare Access team")
        return _keys[kid]


def verify(token: str) -> str:
    """Check a Cloudflare Access token for this app. Returns the logged-in email; raises RemoteAuthError."""
    if not enabled():
        raise RemoteAuthError("phone editing is not configured")
    if not token:
        raise RemoteAuthError("no Cloudflare Access login on this request")
    try:
        claims = jwt.decode(token, _signing_key(token), algorithms=["RS256"], audience=config.CF_ACCESS_AUD,
                            issuer=config.CF_ACCESS_TEAM_DOMAIN, leeway=30,
                            options={"require": ["exp", "iat", "aud", "iss"]})
    except jwt.PyJWTError as exc:
        raise RemoteAuthError(f"invalid token: {exc}") from None
    email = str(claims.get("email") or "").lower()
    if not email:  # service tokens have no email; this app is for a person
        raise RemoteAuthError("token has no email")
    if config.REMOTE_EMAILS and email not in config.REMOTE_EMAILS:
        raise RemoteAuthError(f"{email} is not in REMOTE_EMAILS")
    return email


# ---------- audit log ----------

def record(conn, email: str, method: str, path: str, status: int) -> None:
    """One row per write made through the tunnel."""
    conn.execute("INSERT INTO remote_edits (at, email, method, path, status) VALUES (?, ?, ?, ?, ?)",
                 (now_iso(), email, method, path[:300], status))
    conn.commit()
    log.info("Phone edit by %s: %s %s -> %s", email, method, path[:300], status)


def recent(conn, limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM remote_edits ORDER BY id DESC LIMIT ?", (limit,))]


# ---------- one-time setup ----------

def team_domain() -> str:
    """Your Zero Trust team domain: from .env if already set, else read from where the Pages snapshot's Access
    login redirects to (no extra token permission needed)."""
    from urllib.parse import urlsplit
    from .publish import site_url
    if config.CF_ACCESS_TEAM_DOMAIN:
        return config.CF_ACCESS_TEAM_DOMAIN
    if not config.PAGES_PROJECT:
        raise SystemExit("Set CF_ACCESS_TEAM_DOMAIN in .env (https://<team>.cloudflareaccess.com).")
    r = requests.get(site_url(), allow_redirects=False, timeout=15)
    where = urlsplit(r.headers.get("location", ""))
    if not (where.hostname or "").endswith(".cloudflareaccess.com"):
        raise SystemExit("Couldn't find the team domain: the Pages site didn't redirect to an Access login.")
    return f"https://{where.hostname}"


def setup(host: str, policy_name: str) -> list[str]:
    """Create (once) an Access app for HOST with an existing reusable policy. Returns the .env lines."""
    from .publish import cf_api
    host = host.strip().lower()
    apps = [a for a in cf_api("GET", "/access/apps") if host in (a.get("self_hosted_domains") or [a.get("domain")])]
    if apps:
        app = apps[0]
    else:
        match = [p for p in cf_api("GET", "/access/policies") if p["name"] == policy_name]
        if not match:
            raise SystemExit(f"No reusable Access policy named {policy_name!r}")
        app = cf_api("POST", "/access/apps", json={
            "name": "Philly Events dashboard (editing)",
            "type": "self_hosted",
            "domain": host,
            "self_hosted_domains": [host],
            "session_duration": "720h",
            "app_launcher_visible": False,
            "policies": [{"id": match[0]["id"], "precedence": 1}],
        })
    return [f"REMOTE_HOST={host}", f"CF_ACCESS_TEAM_DOMAIN={team_domain()}", f"CF_ACCESS_AUD={app['aud']}"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setup", nargs=2, metavar=("HOST", "POLICY"), help="create the Access app; print .env lines")
    args = p.parse_args()
    if args.setup:
        print("Add these to .env, then restart the dashboard:")
        print("\n".join(setup(*args.setup)))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
