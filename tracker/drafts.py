"""Create drafts for alerts: LLM text + fact-built on-screen lines + fact-check warnings."""
from __future__ import annotations

import json
import logging

from . import llm
from .store import now_iso

log = logging.getLogger(__name__)

MAX_DRAFTS_PER_RUN = 8  # ~1 min each on the N95; keeps a run under ~10 minutes


def draft_alert(conn, alert_id: int, model: str | None = None) -> int:
    """Generate one draft for an alert. Assumes Ollama is reachable. Returns draft id."""
    alert = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    facts = json.loads(alert["facts"])
    out = llm.generate(facts, model=model)
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
