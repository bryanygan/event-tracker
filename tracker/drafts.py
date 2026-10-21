"""Create drafts for alerts: LLM text (or a template script + LLM hook/caption) + fact-built
on-screen lines + fact-check warnings."""
from __future__ import annotations

import json
import logging

from . import llm, templates
from .store import now_iso

log = logging.getLogger(__name__)

MAX_DRAFTS_PER_RUN = 8  # ~1 min each on the N95; keeps a run under ~10 minutes


def draft_alert(conn, alert_id: int, model: str | None = None) -> int:
    """Generate one draft for an alert. Assumes Ollama is reachable. Returns draft id."""
    alert = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    facts = json.loads(alert["facts"])
    # presale/status: Python writes the voiceover and caption; the model only writes hook + hashtags
    script = templates.script_for(facts)
    caption = templates.caption_for(facts) if script else None
    out = llm.generate(facts, model=model, script=script, caption=caption)
    warnings = llm.check_facts(out, facts)
    if warnings:  # one retry; keep whichever attempt has fewer problems (~1 extra minute)
        retry = llm.generate(facts, model=model, script=script, caption=caption)
        retry_warnings = llm.check_facts(retry, facts)
        if len(retry_warnings) < len(warnings):
            out, warnings = retry, retry_warnings
    # Templated hooks must name the artist/team exactly (one said "THROW" for THROWN) and stay short
    hook_ok = not script or (any(n.lower() in out["hook"].lower() for n in templates.hook_names(facts))
                             and len(out["hook"].split()) <= templates.MAX_HOOK_WORDS)
    if facts.get("format") in ("team_week", "big_game"):
        out["hashtags"] = templates.game_hashtags(facts)
    if script and (warnings or not hook_ok):
        # Templated formats have a known-good fallback hook, so a flagged one is replaced, not shipped.
        out["hook"] = templates.fallback_hook(facts)
        warnings = llm.check_facts(out, facts)
    cur = conn.execute(
        """INSERT INTO drafts (alert_id, format, model, hook, script, caption, hashtags, onscreen, warnings, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (alert_id, facts.get("format", alert["kind"]), out["model"], out["hook"], out["script"], out["caption"],
         " ".join(out["hashtags"]), json.dumps(llm.onscreen_lines(facts)), json.dumps(warnings), now_iso()))
    conn.execute("UPDATE alerts SET status='drafted' WHERE id=? AND status='new'", (alert_id,))
    conn.commit()
    if warnings:
        log.warning("Draft %s for alert %s has fact warnings: %s", cur.lastrowid, alert_id, warnings)
    return cur.lastrowid


def draft_pending(conn, limit: int = MAX_DRAFTS_PER_RUN) -> list[int]:
    """Draft the highest-scoring alerts that don't have a draft yet."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM alerts WHERE status='new' ORDER BY score DESC LIMIT ?", (limit,))]
    if not ids:
        return []
    made = []
    with llm.ollama_session():
        for alert_id in ids:
            try:
                made.append(draft_alert(conn, alert_id))
            except Exception as exc:  # one bad generation shouldn't stop the rest
                log.exception("Drafting alert %s failed: %s", alert_id, exc)
    return made
