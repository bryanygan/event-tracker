"""Trend notes: what you saw trending, typed in by hand, matched to video ideas.

There is no trend API we may use (no TikTok scraping), so this is creator-supplied evidence: a sound, search
term, hashtag or topic you noticed, when you saw it, how far it reaches (Philly / national / unknown) and how
sure you are. Notes expire (TREND_NOTE_DAYS by default) because a week-old trend isn't evidence of anything.

A note matches an idea when one of its terms *equals* (casefolded) an artist, genre, venue or team on the idea,
or, for keyword terms, appears as a whole word in the idea's title. No fuzzy matching: "Drake" must not pick
up "Drake Bell"'s show by similarity. (A whole-word title hit still can, so those count for less and the card
says which term matched.) Matched, unexpired notes fill the opportunity score's "search" component; national
or unknown-scope notes are labelled as such, never presented as Philly demand. No note → the component stays
unknown, exactly as before notes existed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from . import config
from .posts import PostError, parse_iso
from .store import now_iso

KINDS = ("sound", "search", "hashtag", "topic", "other")
SCOPES = ("philly", "national", "unknown")
CONFIDENCE = ("low", "medium", "high")
MOMENTUM = ("rising", "stable", "uncertain")
MAX_TEXT, MAX_NOTES, MAX_TERM, MAX_TERMS = 200, 1000, 80, 12

# Component value for a matched note: confidence sets the level, scope discounts what isn't Philly-specific.
CONFIDENCE_VALUE = {"high": 100, "medium": 75, "low": 50}
SCOPE_FACTOR = {"philly": 1.0, "national": 0.7, "unknown": 0.6}
TITLE_MATCH_FACTOR = 0.8  # a whole-word hit in the title is weaker evidence than an exact artist/genre/venue match
SCOPE_LABEL = {"philly": "Philly", "national": "National (not Philly-specific)", "unknown": "Scope unknown"}


class TrendError(ValueError):
    pass


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def _utc(value: str) -> str:
    try:
        return parse_iso(value).replace(microsecond=0).isoformat()
    except PostError as exc:
        raise TrendError(str(exc)) from None


def validate(fields: dict, partial: bool = False) -> dict:
    """Checked, normalized fields for the trend_notes table. Raises TrendError with a readable message."""
    out = {}
    for key, allowed in (("kind", KINDS), ("scope", SCOPES), ("confidence", CONFIDENCE), ("momentum", MOMENTUM)):
        if key in fields:
            v = fields[key]
            if v is None and key == "momentum":
                out[key] = None
            elif v not in allowed:
                raise TrendError(f"{key} must be one of {', '.join(allowed)}")
            else:
                out[key] = v
    if "text" in fields:
        text = (fields["text"] or "").strip()
        if not text:
            raise TrendError("text can't be empty: what did you see trending?")
        if len(text) > MAX_TEXT:
            raise TrendError(f"text is too long (max {MAX_TEXT} characters)")
        out["text"] = text
    if "notes" in fields:
        notes = (fields["notes"] or "").strip() or None
        if notes and len(notes) > MAX_NOTES:
            raise TrendError(f"notes are too long (max {MAX_NOTES} characters)")
        out["notes"] = notes
    if "source_url" in fields:
        url = (fields["source_url"] or "").strip() or None
        if url and (not re.match(r"^https?://[^\s/]+", url, re.I) or len(url) > 500):
            raise TrendError("source_url must be an http(s) link")
        out["source_url"] = url
    if "terms" in fields:
        terms = fields["terms"]
        if isinstance(terms, str):
            terms = terms.split(",")
        if not isinstance(terms, list):
            raise TrendError("terms must be a list of names/keywords")
        clean = list(dict.fromkeys(" ".join(str(t).split()) for t in terms if str(t).strip()))
        if not clean:
            raise TrendError("add at least one match term (an artist, genre, venue or keyword)")
        if len(clean) > MAX_TERMS or any(len(t) > MAX_TERM for t in clean):
            raise TrendError(f"at most {MAX_TERMS} terms of up to {MAX_TERM} characters")
        out["terms"] = json.dumps(clean)
    for key in ("observed_at", "expires_at"):
        if fields.get(key):
            out[key] = _utc(fields[key])
        elif key in fields and key == "expires_at":
            out[key] = None  # filled from observed_at below / on create
    if not partial:
        missing = [k for k in ("kind", "text", "scope", "confidence", "terms") if k not in out]
        if missing:
            raise TrendError(f"missing: {', '.join(missing)}")
    return out


def _row(r, now: datetime) -> dict:
    d = dict(r)
    d["terms"] = json.loads(d["terms"])
    d["expired"] = datetime.fromisoformat(d["expires_at"]) <= now
    return d


def create_note(conn, now: datetime | None = None, **fields) -> int:
    now = now or datetime.now(timezone.utc)
    f = validate(fields)
    f.setdefault("observed_at", now.replace(microsecond=0).isoformat())
    if not f.get("expires_at"):
        f["expires_at"] = (datetime.fromisoformat(f["observed_at"]) + timedelta(days=config.TREND_NOTE_DAYS)).isoformat()
    if f["expires_at"] <= f["observed_at"]:
        raise TrendError("expires_at must be after observed_at")
    f["created_at"] = now_iso()
    cur = conn.execute(f"INSERT INTO trend_notes ({', '.join(f)}) VALUES ({', '.join('?' * len(f))})", list(f.values()))
    conn.commit()
    return cur.lastrowid


def update_note(conn, note_id: int, **fields) -> None:
    row = conn.execute("SELECT * FROM trend_notes WHERE id=?", (note_id,)).fetchone()
    if not row:
        raise LookupError(note_id)
    f = validate(fields, partial=True)
    if "expires_at" in f and f["expires_at"] is None:
        f["expires_at"] = (datetime.fromisoformat(f.get("observed_at") or row["observed_at"])
                           + timedelta(days=config.TREND_NOTE_DAYS)).isoformat()
    if (f.get("expires_at") or row["expires_at"]) <= (f.get("observed_at") or row["observed_at"]):
        raise TrendError("expires_at must be after observed_at")
    if f:
        f["updated_at"] = now_iso()
        conn.execute(f"UPDATE trend_notes SET {', '.join(f'{k}=?' for k in f)} WHERE id=?", [*f.values(), note_id])
        conn.commit()


def delete_note(conn, note_id: int) -> None:
    if not conn.execute("DELETE FROM trend_notes WHERE id=?", (note_id,)).rowcount:
        raise LookupError(note_id)
    conn.commit()


def list_notes(conn, now: datetime | None = None, include_expired: bool = True) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = [_row(r, now) for r in conn.execute("SELECT * FROM trend_notes ORDER BY observed_at DESC, id DESC")]
    return rows if include_expired else [r for r in rows if not r["expired"]]


def idea_terms(facts: dict, events: list[dict]) -> set[str]:
    """Exact-match targets for an idea: artists, genres, venues, teams (casefolded)."""
    shows = facts.get("shows") or ([facts["show"]] if facts.get("show") else [])
    names = [facts.get(k) for k in ("artist", "genre", "team", "opponent")]
    for s in shows:
        names += [s.get("artist"), s.get("venue"), s.get("opponent")]
    for e in events:
        names += [e.get("headliner"), e.get("genre"), e.get("venue_name")]
    return {_norm(n) for n in names if isinstance(n, str) and n.strip()}


def match(note: dict, terms: set[str], title: str) -> tuple[str, str] | None:
    """(term, 'exact'|'title') for the best match of this note on an idea, else None."""
    for t in note["terms"]:
        if _norm(t) in terms:
            return t, "exact"
    for t in note["terms"]:
        if re.search(rf"(?<!\w){re.escape(_norm(t))}(?!\w)", _norm(title)):
            return t, "title"
    return None


def search_component(notes: list[dict], facts: dict, events: list[dict], title: str) -> tuple[float | None, str, list[dict]]:
    """(value or None, why, matched notes incl. expired ones for display). Only unexpired notes count."""
    terms = idea_terms(facts, events)
    matched = []
    for n in notes:
        m = match(n, terms, title)
        if m:
            value = CONFIDENCE_VALUE[n["confidence"]] * SCOPE_FACTOR[n["scope"]] * (1 if m[1] == "exact" else TITLE_MATCH_FACTOR)
            matched.append({"id": n["id"], "kind": n["kind"], "text": n["text"], "scope": n["scope"],
                            "confidence": n["confidence"], "observed_at": n["observed_at"], "expires_at": n["expires_at"],
                            "expired": n["expired"], "matched_term": m[0], "match": m[1], "value": round(value)})
    live = sorted((m for m in matched if not m["expired"]), key=lambda m: -m["value"])
    if not live:
        return None, "Unknown: no trend notes match this idea", matched
    best = live[0]
    more = f" (+{len(live) - 1} more)" if len(live) > 1 else ""
    why = (f"{SCOPE_LABEL[best['scope']]} {best['kind']} trend, {best['confidence']} confidence: "
           f"“{best['text']}” (matched “{best['matched_term']}”){more}")
    return best["value"], why, matched
