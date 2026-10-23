"""Create drafts for alerts: LLM text (or a template script + LLM hook/caption) + fact-built
on-screen lines + fact-check warnings."""
from __future__ import annotations

import json
import logging

from . import angles, detectors, llm, templates
from .store import now_iso

log = logging.getLogger(__name__)

MAX_DRAFTS_PER_RUN = 8  # ~1 min each with Ollama on the N95 (seconds with Claude); keeps a run under ~10 minutes


def draft_row(r, alert_facts: dict) -> dict:
    """A drafts row for the dashboard: JSON fields parsed, plus what changed in the alert since it was written."""
    d = dict(r)
    d["onscreen"] = json.loads(d["onscreen"] or "[]")
    d["warnings"] = json.loads(d["warnings"] or "[]")
    written_from = d.pop("facts")
    d["facts_changes"] = detectors.facts_diff(json.loads(written_from), alert_facts) if written_from else []
    return d


def needs_llm(facts: dict, angle: str | None) -> bool:
    """Templated formats drafted from a chosen angle need no model at all (hook, script, caption, tags
    all come from Python), so the dashboard doesn't start Ollama for them."""
    return not (angle and templates.script_for(facts))


def _quiet(step: str, extra_generation: bool = False) -> None:
    pass


def draft_alert(conn, alert_id: int, model: str | None = None, angle: str | None = None, progress=_quiet) -> int:
    """Generate one draft for an alert, optionally from a chosen angle (angles.py). Assumes Ollama is
    reachable when needs_llm(). Returns draft id. `progress(step, extra_generation)` hears what it's doing,
    for the dashboard's progress bar; extra_generation=True means a retry just added another model call."""
    alert = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    facts = json.loads(alert["facts"])
    chosen = angles.find(facts, angle) if angle else None
    if angle and chosen is None:
        raise ValueError(f"no angle {angle!r} for this alert")
    facts = angles.scoped_facts(facts, chosen)  # "picks": the draft only sees (and is checked against) the picks
    # presale/status: Python writes the voiceover and caption; the model only writes hook + hashtags
    script = templates.script_for(facts)
    caption = templates.caption_for(facts) if script else None
    if chosen and script:
        out = {"hook": chosen["hook"], "script": script, "caption": caption,
               "hashtags": angles.template_hashtags(facts), "model": "template"}
        progress("Saving the draft")
        return _save(conn, alert, facts, out, llm.check_facts(out, facts), angle)
    out = llm.generate(facts, model=model, script=script, caption=caption, conn=conn,
                       **({"angle": chosen} if chosen else {}))
    progress("Checking the draft against the facts")
    warnings = llm.check_facts(out, facts)
    if warnings:  # one retry; keep whichever attempt has fewer problems (~1 extra minute)
        progress("Second try: the first draft didn't match the facts", extra_generation=True)
        retry = llm.generate(facts, model=model, script=script, caption=caption, conn=conn)
        retry_warnings = llm.check_facts(retry, facts)
        if len(retry_warnings) < len(warnings):
            out, warnings = retry, retry_warnings
    # Templated hooks must name the artist/team exactly (one said "THROW" for THROWN) and stay short
    hook_ok = chosen is not None or not script or (any(n.lower() in out["hook"].lower() for n in templates.hook_names(facts))
                             and len(out["hook"].split()) <= templates.MAX_HOOK_WORDS)
    if facts.get("format") in ("team_week", "big_game"):
        out["hashtags"] = templates.game_hashtags(facts)
    if script and (warnings or not hook_ok):
        # Templated formats have a known-good fallback hook, so a flagged one is replaced, not shipped.
        out["hook"] = templates.fallback_hook(facts)
        warnings = llm.check_facts(out, facts)
    progress("Saving the draft")
    return _save(conn, alert, facts, out, warnings, angle)


def _save(conn, alert, facts: dict, out: dict, warnings: list[str], angle: str | None) -> int:
    if templates.comeback(facts):  # not an error, but crowd-sourced data deserves a second look
        warnings.append(f"Verify: last Philly show {facts['history']['last_philly_show']} "
                        f"({facts['history']['last_philly_venue']}) is from setlist.fm, which is crowd-sourced")
    cur = conn.execute(
        """INSERT INTO drafts (alert_id, format, model, hook, script, caption, hashtags, onscreen, warnings, created_at,
                               facts, angle)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (alert["id"], facts.get("format", alert["kind"]), out["model"], out["hook"], out["script"], out["caption"],
         " ".join(out["hashtags"]), json.dumps(llm.onscreen_lines(facts)), json.dumps(warnings), now_iso(),
         alert["facts"], angle))
    conn.execute("UPDATE alerts SET status='drafted' WHERE id=? AND status='new'", (alert["id"],))
    conn.commit()
    if warnings:
        log.warning("Draft %s for alert %s has fact warnings: %s", cur.lastrowid, alert["id"], warnings)
    return cur.lastrowid


def draft_pending(conn, limit: int = MAX_DRAFTS_PER_RUN) -> list[int]:
    """Draft the highest-scoring alerts that don't have a draft yet."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM alerts WHERE status='new' ORDER BY score DESC LIMIT ?", (limit,))]
    if not ids:
        return []
    made = []
    with llm.model_session():
        for alert_id in ids:
            try:
                made.append(draft_alert(conn, alert_id))
            except Exception as exc:  # one bad generation shouldn't stop the rest
                log.exception("Drafting alert %s failed: %s", alert_id, exc)
    return made
