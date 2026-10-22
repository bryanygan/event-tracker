"""Web context for a video idea: "what's new with this artist" from Perplexity, with dated sources.

This is reading material for you, not facts. Nothing here goes into scores, angles or drafts (the
fact checker only trusts our own data); the card shows it labelled as web context with its links.

Cost control (~$0.0016 per lookup on the `fast` preset, measured 2026-10-22):
- one lookup per artist is reused for CONTEXT_CACHE_DAYS;
- at most CONTEXT_DAILY_CAP lookups per ET day, counting failures (they cost a request too);
- the pipeline adds at most CONTEXT_AUTO_PER_DAY of those, and only for "strong" queue ideas.

Only public event facts are sent (artist, show date, venue), never listening history.
Perplexity API terms (checked 2026-10-22): output is ours to show in our own app, no attribution clause.
API: POST /v1/agent (Agent API; it replaced Sonar chat completions). On the `fast` preset the answer is in
output[type=message].content[].text with [n] citations into output[type=search_results].results[].id;
the top-level `output_text` came back null in testing.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from . import config, opportunities
from .store import now_iso

log = logging.getLogger(__name__)
TZ = ZoneInfo(config.TIMEZONE)
URL = "https://api.perplexity.ai/v1/agent"
PROVIDER = "perplexity/fast"
# Single-artist formats. Roundups and games have no one subject worth a lookup.
FORMATS = ("presale", "new_tour", "status_change")

PROMPT = ("Today is {today}. {artist} has a Philadelphia show on {show}. "
          "For a Philly concert TikTok creator, list up to 3 recent things (from roughly {since} to today) "
          "that make this show timely: new releases, tour announcements, viral moments, Philly connections. "
          "Give the date of each item. Use only what search results support; if you find nothing recent, say so. "
          "Keep it under 90 words.")


class ContextError(Exception):
    """Shown to the user as-is (no key, cap reached, provider error)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def subject(facts: dict) -> str | None:
    """Cache key: the artist, for formats where a lookup makes sense."""
    if facts.get("format") in FORMATS and facts.get("artist"):
        return facts["artist"].casefold()
    return None


def build_prompt(facts: dict, now: datetime) -> str:
    local = now.astimezone(TZ)
    show = (facts.get("shows") or [facts.get("show") or {}])[0]
    where = f"{show.get('date', 'a date TBA')}" + (f" at {show['venue']}" if show.get("venue") else "")
    since = local - timedelta(days=60)
    return PROMPT.format(today=f"{local:%A, %B} {local.day}, {local.year}", artist=facts["artist"], show=where,
                         since=f"{since:%B} {since.day}, {since.year}")


def parse(body: dict) -> tuple[str, list[dict]]:
    """(answer text, cited sources). Sources keep Perplexity's [n] ids so the text's citations still match."""
    text, results = "", []
    for item in body.get("output") or []:
        if item.get("type") == "message":
            text += "".join(c.get("text") or "" for c in item.get("content") or [])
        elif item.get("type") == "search_results":
            results += item.get("results") or []
    text = text or body.get("output_text") or ""
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    sources = [{"id": r.get("id"), "title": r.get("title"), "url": r.get("url"), "date": r.get("date")}
               for r in results if r.get("url") and (r.get("id") in cited or not cited)]
    return text.strip(), sources[:8]


def _et_day_utc(now: datetime) -> tuple[str, str]:
    """[start, end) of now's ET calendar day, as UTC ISO strings comparable with created_at."""
    start = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=1, hours=2)).replace(hour=0)  # +2h survives a 25-hour DST day
    return tuple(t.astimezone(timezone.utc).isoformat(timespec="seconds") for t in (start, end))


def usage(conn, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    row = conn.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(trigger='auto'), 0) AS auto,
                                 COALESCE(SUM(cost_usd), 0) AS cost FROM web_context
                          WHERE created_at >= ? AND created_at < ?""", _et_day_utc(now)).fetchone()
    return {"today": row["n"], "auto_today": row["auto"], "cap": config.CONTEXT_DAILY_CAP,
            "auto_cap": config.CONTEXT_AUTO_PER_DAY, "cost_today_usd": round(row["cost"], 4),
            "enabled": bool(config.PERPLEXITY_API_KEY)}


def cached(conn, subj: str | None, now: datetime | None = None) -> dict | None:
    """Latest successful lookup for this artist, with `fresh` (inside the cache window) set."""
    if not subj:
        return None
    r = conn.execute("""SELECT * FROM web_context WHERE subject=? AND error IS NULL
                        ORDER BY created_at DESC LIMIT 1""", (subj,)).fetchone()
    if not r:
        return None
    now = now or datetime.now(timezone.utc)
    age = now - datetime.fromisoformat(r["created_at"])
    return {"id": r["id"], "answer": r["answer"], "sources": json.loads(r["sources"]), "provider": r["provider"],
            "created_at": r["created_at"], "fresh": age <= timedelta(days=config.CONTEXT_CACHE_DAYS)}


def lookup(conn, alert_id: int, refresh: bool = False, trigger: str = "manual",
           now: datetime | None = None, session=requests) -> dict:
    """Context for an alert: the cached lookup if fresh (free), else one Perplexity call."""
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not row:
        raise ContextError("no such alert", 404)
    facts = json.loads(row["facts"])
    subj = subject(facts)
    if not subj:
        raise ContextError("web context is only for single-artist ideas (presales, new shows, status changes)", 400)
    hit = cached(conn, subj, now)
    if hit and hit["fresh"] and not refresh:
        return hit
    if not config.PERPLEXITY_API_KEY:
        raise ContextError("PERPLEXITY_API_KEY isn't set in .env", 503)
    used = usage(conn, now)
    if used["today"] >= config.CONTEXT_DAILY_CAP:
        raise ContextError(f"daily limit reached ({config.CONTEXT_DAILY_CAP} lookups); try again tomorrow", 429)

    prompt = build_prompt(facts, now)
    answer, sources, cost, error = "", [], None, None
    try:
        r = session.post(URL, timeout=60,
                         headers={"Authorization": f"Bearer {config.PERPLEXITY_API_KEY}", "Content-Type": "application/json"},
                         json={"preset": "fast", "input": prompt, "tools": [{"type": "web_search"}]})
        body = r.json() if r.content else {}
        if r.status_code != 200:
            error = f"Perplexity returned {r.status_code}: {(body.get('error') or {}).get('message', '')}"[:300]
        else:
            answer, sources = parse(body)
            cost = ((body.get("usage") or {}).get("cost") or {}).get("total_cost")
            if not answer:
                error = "Perplexity returned no answer"
    except (requests.RequestException, ValueError) as exc:
        error = f"Perplexity request failed: {exc.__class__.__name__}"
    cur = conn.execute(
        """INSERT INTO web_context (subject, alert_id, provider, prompt, answer, sources, cost_usd, error, trigger, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (subj, alert_id, PROVIDER, prompt, answer, json.dumps(sources), cost, error, trigger,
         now.isoformat(timespec="seconds")))
    conn.commit()
    if error:
        log.warning("Web context for alert %s failed: %s", alert_id, error)
        raise ContextError(error, 502)
    log.info("Web context for alert %s (%s): %d sources, $%s", alert_id, trigger, len(sources), cost)
    return {**cached(conn, subj, now), "id": cur.lastrowid}


def auto_lookup(conn, now: datetime | None = None) -> int:
    """Pipeline step: look up the strongest active ideas that have no fresh context, within the daily caps."""
    now = now or datetime.now(timezone.utc)
    if not config.PERPLEXITY_API_KEY:
        return 0
    used = usage(conn, now)
    budget = min(config.CONTEXT_AUTO_PER_DAY - used["auto_today"], config.CONTEXT_DAILY_CAP - used["today"])
    done, seen = 0, set()
    for item in opportunities.queue(conn, now):
        if done >= budget:
            break
        if item["band"] != "strong" or item["stage"] not in opportunities.ACTIVE_STAGES:
            continue
        facts = json.loads(conn.execute("SELECT facts FROM alerts WHERE id=?", (item["alert_id"],)).fetchone()["facts"])
        subj = subject(facts)
        if not subj or subj in seen or (cached(conn, subj, now) or {}).get("fresh"):
            continue
        seen.add(subj)
        try:
            lookup(conn, item["alert_id"], trigger="auto", now=now)
        except ContextError:
            pass  # logged; counts against the cap like any call
        done += 1
    return done
