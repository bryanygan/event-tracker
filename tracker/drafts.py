"""Create drafts for alerts: LLM text (or a template script + LLM hook/caption) + fact-built
on-screen lines + fact-check warnings."""
from __future__ import annotations

import json
import logging

from . import angles, config, detectors, llm, music_formats, redraft, templates
from .store import now_iso

log = logging.getLogger(__name__)

MAX_DRAFTS_PER_RUN = 8  # ~1 min each with Ollama on the N95; keeps a run under ~10 minutes
MAX_CLAUDE_DRAFTS_PER_RUN = 20  # seconds each with Claude; CLAUDE_DAILY_CAP still bounds the spend


def draft_row(r, alert_facts: dict, conn=None) -> dict:
    """A drafts row for the dashboard: JSON fields parsed, plus what changed in the alert since it was written.
    With `conn`, a removed show says why (redraft.removal_reasons), and your own edits (redraft.py) aren't
    reported back as changes: a show you left out that the idea later drops too, or one you added that it picks up."""
    d = dict(r)
    d["onscreen"] = json.loads(d["onscreen"] or "[]")
    d["warnings"] = json.loads(d["warnings"] or "[]")
    d["instructions"] = ins = json.loads(d.get("instructions") or "null")
    d["sources"] = json.loads(d.get("sources") or "null")  # vinyl stories: sources and checked claims
    written_from = d.pop("facts")
    if not written_from:
        d["facts_changes"] = []
        return d
    written = json.loads(written_from)
    changes = detectors.facts_diff(written, alert_facts)
    if ins:
        mine = {f"Removed: {x}" for x in ins.get("left_out", [])} | {f"Added: {x}" for x in ins.get("added", [])}
        changes = [c for c in changes if c not in mine]
    if conn is not None:
        changes = redraft.explain(changes, redraft.removal_reasons(conn, written, alert_facts))
        if ins and ins.get("add"):
            changes += redraft.apply(conn, alert_facts, ins)[1]
    d["facts_changes"] = changes
    return d


def _script_for(facts: dict) -> str | None:
    return music_formats.script_for(facts) if music_formats.is_music(facts) else templates.script_for(facts)


def needs_llm(facts: dict, angle: str | None) -> bool:
    """Templated formats drafted from a chosen angle need no model at all (hook, script, caption, tags
    all come from Python), so the dashboard doesn't start Ollama for them."""
    return not (angle and _script_for(facts))


def _quiet(step: str, extra_generation: bool = False) -> None:
    pass


def _note(note: str | None) -> dict:
    """generate()'s `note` only when you wrote one, so a plain draft's call is unchanged."""
    return {"note": note} if note else {}


def draft_alert(conn, alert_id: int, model: str | None = None, angle: str | None = None, progress=_quiet,
                instructions: dict | None = None, deep: bool = False) -> int:
    """Generate one draft for an alert, optionally from a chosen angle (angles.py). Assumes Ollama is
    reachable when needs_llm(). Returns draft id. `progress(step, extra_generation)` hears what it's doing,
    for the dashboard's progress bar; extra_generation=True means a retry just added another model call.
    `instructions`: your note and the shows you added or left out (redraft.validate). `deep`: vinyl only, add a
    web-search pass to the research."""
    alert = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    facts = json.loads(alert["facts"])
    if alert["kind"] == config.VINYL_KIND:  # researched and written by Claude (vinyl_research.py)
        from . import vinyl_research
        return vinyl_research.draft(conn, alert, facts, progress, instructions, deep=deep)
    if music_formats.is_music(facts):
        return _draft_music(conn, alert, facts, model, angle, progress, instructions)
    chosen = angles.find(facts, angle) if angle else None
    if angle and chosen is None:
        raise ValueError(f"no angle {angle!r} for this alert")
    facts = angles.scoped_facts(facts, chosen)  # "picks": the draft only sees (and is checked against) the picks
    edited, problems = redraft.apply(conn, facts, instructions)
    instructions = _with_labels(instructions, facts, edited)
    facts = edited
    note = (instructions or {}).get("note")
    problems += redraft.note_warnings(conn, facts, note)
    # presale/status: Python writes the voiceover and caption; the model only writes hook + hashtags
    script = templates.script_for(facts)
    caption = templates.caption_for(facts) if script else None
    if chosen and script:
        out = {"hook": chosen["hook"], "script": script, "caption": caption,
               "hashtags": angles.template_hashtags(facts), "model": "template"}
        progress("Saving the draft")
        return _save(conn, alert, facts, out, llm.check_facts(out, facts), angle)
    try:
        out = llm.generate(facts, model=model, script=script, caption=caption, conn=conn, **_note(note),
                           **({"angle": chosen} if chosen else {}))
    except llm.LowMemory:
        if not script:
            raise
        out = {"hook": templates.fallback_hook(facts), "script": script, "caption": caption,
               "hashtags": angles.template_hashtags(facts), "model": "template"}
        progress("Saving the draft")
        return _save(conn, alert, facts, out, llm.check_facts(out, facts) + problems, angle, instructions)
    progress("Checking the draft against the facts")
    warnings = llm.check_facts(out, facts)
    if warnings:  # one retry; keep whichever attempt has fewer problems (~1 extra minute)
        progress("Second try: the first draft didn't match the facts", extra_generation=True)
        retry = llm.generate(facts, model=model, script=script, caption=caption, conn=conn, **_note(note))
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
    return _save(conn, alert, facts, out, warnings + problems, angle, instructions)


def _with_labels(ins: dict | None, before: dict, after: dict) -> dict | None:
    """The instructions as saved on the draft, plus readable labels of what was added and left out."""
    if not ins:
        return None
    was = {redraft.show_key(s): s for s in before.get("shows") or []}
    now = {redraft.show_key(s): s for s in after.get("shows") or []}
    return {**ins, "added": [redraft.label(s) for k, s in now.items() if k not in was],
            "left_out": [redraft.label(s) for k, s in was.items() if k not in now]}


def _draft_music(conn, alert, facts: dict, model: str | None, angle: str | None, progress,
                 instructions: dict | None = None) -> int:
    """Music ideas (music_formats.py): roundups have a templated script (the model writes only a hook and tags,
    or nothing when you pick an angle); a story is written by the model around the status it has to state."""
    chosen = angles.find(facts, angle) if angle else None
    if angle and chosen is None:
        raise ValueError(f"no angle {angle!r} for this alert")
    script = music_formats.script_for(facts)
    caption = music_formats.caption_for(facts)
    if chosen and script:
        out = {"hook": chosen["hook"], "script": script, "caption": caption,
               "hashtags": music_formats.hashtags(facts), "model": "template"}
        progress("Saving the draft")
        return _save(conn, alert, facts, out, llm.check_facts(out, facts), angle)
    note = (instructions or {}).get("note")
    try:
        out = llm.generate(facts, model=model, script=script, caption=caption, conn=conn, **_note(note),
                           **({"angle": chosen} if chosen else {}))
    except llm.LowMemory:
        if not script:  # a story needs a model to write it; it stays in the queue for a retry
            raise
        out = {"hook": music_formats.fallback_hook(facts), "script": script, "caption": caption,
               "hashtags": music_formats.hashtags(facts), "model": "template"}
        progress("Saving the draft")
        return _save(conn, alert, facts, out, llm.check_facts(out, facts), angle, instructions)
    progress("Checking the draft against the facts")
    warnings = llm.check_facts(out, facts)
    if warnings and not script:  # one retry for a model-written story; keep the attempt with fewer problems
        progress("Second try: the first draft didn't match the facts", extra_generation=True)
        retry = llm.generate(facts, model=model, conn=conn, **_note(note), **({"angle": chosen} if chosen else {}))
        retry_warnings = llm.check_facts(retry, facts)
        if len(retry_warnings) < len(warnings):
            out, warnings = retry, retry_warnings
    names = music_formats.hook_names(facts)
    hook_ok = (any(n and n.lower() in out["hook"].lower() for n in names)
               and len(out["hook"].split()) <= templates.MAX_HOOK_WORDS)
    if not chosen and (not hook_ok or any(w.split(": ", 1)[-1].lower() in out["hook"].lower() for w in warnings)):
        out["hook"] = music_formats.fallback_hook(facts)  # built from facts: right by construction
        warnings = llm.check_facts(out, facts)
    if script and not out["hashtags"]:
        out["hashtags"] = music_formats.hashtags(facts)
    progress("Saving the draft")
    return _save(conn, alert, facts, out, warnings, angle, instructions)


def _save(conn, alert, facts: dict, out: dict, warnings: list[str], angle: str | None,
          instructions: dict | None = None) -> int:
    if templates.comeback(facts):  # not an error, but crowd-sourced data deserves a second look
        warnings.append(f"Verify: last Philly show {facts['history']['last_philly_show']} "
                        f"({facts['history']['last_philly_venue']}) is from setlist.fm, which is crowd-sourced")
    cur = conn.execute(
        """INSERT INTO drafts (alert_id, format, model, hook, script, caption, hashtags, onscreen, warnings, created_at,
                               facts, angle, instructions)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (alert["id"], facts.get("format", alert["kind"]), out["model"], out["hook"], out["script"], out["caption"],
         " ".join(out["hashtags"]), json.dumps(llm.onscreen_lines(facts)), json.dumps(warnings), now_iso(),
         alert["facts"], angle, json.dumps(instructions) if instructions else None))
    conn.execute("UPDATE alerts SET status='drafted' WHERE id=? AND status='new'", (alert["id"],))
    conn.commit()
    if warnings:
        log.warning("Draft %s for alert %s has fact warnings: %s", cur.lastrowid, alert["id"], warnings)
    return cur.lastrowid


def draft_pending(conn, limit: int | None = None) -> list[int]:
    """Draft the highest-scoring alerts that don't have a draft yet. Vinyl ideas wait for your click: their
    researched drafts cost more than an event blurb."""
    if limit is None:
        limit = MAX_CLAUDE_DRAFTS_PER_RUN if llm.uses_claude() else MAX_DRAFTS_PER_RUN
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM alerts WHERE status='new' AND kind != ? ORDER BY score DESC LIMIT ?",
        (config.VINYL_KIND, limit))]
    return _draft_all(conn, ids)


def _draft_all(conn, ids: list[int], claude_only: bool = False) -> list[int]:
    made = []
    if not ids:
        return made
    with llm.model_session():
        for alert_id in ids:
            # A draft can take two calls (a retry); stop before the cap would send the rest to Ollama
            if claude_only and llm.claude_calls_today(conn) > config.CLAUDE_DAILY_CAP - 2:
                log.info("Stopping at the Claude daily cap; %d alerts left", len(ids) - len(made))
                break
            if claude_only and llm._batch and llm._batch["claude_failed"]:  # Claude failed: don't redo the rest locally
                log.warning("Claude failed; stopping with %d alerts left", len(ids) - len(made))
                break
            try:
                made.append(draft_alert(conn, alert_id))
            except llm.LowMemory:  # already logged once; templated ideas still get drafts
                log.info("Alert %s left undrafted: not enough free memory for the local model", alert_id)
            except Exception as exc:  # one bad generation shouldn't stop the rest
                log.exception("Drafting alert %s failed: %s", alert_id, exc)
    return made


def local_drafted(conn, now: str | None = None) -> list[int]:
    """Live alerts whose newest draft came from the local model (written before Claude drafted), best first."""
    now = now or now_iso()
    return [r["id"] for r in conn.execute(
        """SELECT a.id FROM alerts a
           JOIN drafts d ON d.id = (SELECT MAX(id) FROM drafts WHERE alert_id = a.id)
           WHERE a.status = 'drafted' AND (a.expires_at IS NULL OR a.expires_at > ?)
             AND d.model NOT LIKE '%claude%' AND d.model != 'template'
           ORDER BY a.score DESC""", (now,))]


def redraft_local(conn) -> list[int]:
    """One-off: new Claude drafts for alerts whose newest draft is a local-model one. The old drafts stay."""
    if not llm.uses_claude():
        raise SystemExit("Claude isn't set up (ANTHROPIC_API_KEY / DRAFT_PROVIDER); nothing to redraft with.")
    ids = local_drafted(conn)
    log.info("Redrafting %d alerts with Claude", len(ids))
    return _draft_all(conn, ids, claude_only=True)
