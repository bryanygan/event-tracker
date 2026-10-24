"""Events you add yourself (creator-roadmap P2-14): the food festivals, pop-ups and markets that live on
Eventbrite or Instagram, which we can't read automatically (docs/agent-notes/experience-sources.md).

Every fact is typed by you: name, dates, time, place, price. Nothing is looked up or guessed, so the "facts come
from data" rule holds: here the data is you. Saving one puts it in `events` right away (as "man:<id>", segment
Experience) and raises its idea, so it shows in What to post without waiting for the next run.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from . import config, detectors, store
from .fetchers import experiences

FIELDS = ("name", "category", "start_date", "end_date", "start_time", "venue", "city", "price_min", "price_max",
          "url", "notes")


class ManualEventError(ValueError):
    """Bad input from the dashboard (shown as a 400)."""


def _clean(fields: dict) -> dict:
    out = {k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items() if k in FIELDS or k == "free"}
    out = {k: (None if v == "" else v) for k, v in out.items()}
    if not out.get("name"):
        raise ManualEventError("Name is required")
    if not out.get("venue"):
        raise ManualEventError("Where is it? (venue or place)")
    try:
        start = date.fromisoformat(out.get("start_date") or "")
    except ValueError:
        raise ManualEventError("Start date must be YYYY-MM-DD") from None
    if out.get("end_date"):
        try:
            end = date.fromisoformat(out["end_date"])
        except ValueError:
            raise ManualEventError("End date must be YYYY-MM-DD") from None
        if end < start:
            raise ManualEventError("End date is before the start date")
        if end == start:
            out["end_date"] = None
    if out.get("start_time") and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", out["start_time"]):
        raise ManualEventError("Time must be HH:MM (24-hour)")
    if out.get("url") and not re.match(r"https?://", out["url"]):
        raise ManualEventError("Link must start with http:// or https://")
    if out.get("category") and out["category"] not in config.EXPERIENCE_CATEGORIES:
        raise ManualEventError(f"Category must be one of {', '.join(config.EXPERIENCE_CATEGORIES)}")
    if out.pop("free", False):  # the only way a hand-added event is ever called free: you ticked it
        out["price_min"] = out["price_max"] = 0.0
    for k in ("price_min", "price_max"):
        if out.get(k) is not None:
            try:
                out[k] = float(out[k])
            except (TypeError, ValueError):
                raise ManualEventError("Prices must be numbers") from None
            if out[k] < 0:
                raise ManualEventError("Prices can't be negative")
    if out.get("price_min") is None and out.get("price_max") is not None:
        out["price_min"] = out["price_max"]
    if out.get("price_max") is not None and out["price_max"] < out["price_min"]:
        raise ManualEventError("Highest price is below the lowest")
    return out


def list_events(conn, include_archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM manual_events" + ("" if include_archived else " WHERE archived=0")
    return [dict(r) for r in conn.execute(sql + " ORDER BY start_date, id")]


def create(conn, **fields) -> int:
    f = _clean(fields)
    cols = [k for k in FIELDS if k in f]
    cur = conn.execute(f"INSERT INTO manual_events ({', '.join(cols)}, created_at) VALUES ({', '.join('?' * len(cols))}, ?)",
                       [f[k] for k in cols] + [store.now_iso()])
    sync(conn, cur.lastrowid)
    return cur.lastrowid


def update(conn, event_id: int, **fields) -> None:
    row = conn.execute("SELECT * FROM manual_events WHERE id=? AND archived=0", (event_id,)).fetchone()
    if row is None:
        raise KeyError(event_id)
    merged = {**{k: row[k] for k in FIELDS}, **fields}
    if "free" not in fields and row["price_min"] == 0 and not row["price_max"] and \
            "price_min" not in fields and "price_max" not in fields:
        merged["free"] = True  # an edit to something else keeps it free
    f = _clean(merged)
    conn.execute(f"UPDATE manual_events SET {', '.join(f'{k}=?' for k in FIELDS)}, updated_at=? WHERE id=?",
                 [f.get(k) for k in FIELDS] + [store.now_iso(), event_id])
    sync(conn, event_id)


def archive(conn, event_id: int) -> None:
    """Take it off the list: the event stops showing and its idea is dismissed unless you've posted it."""
    if not conn.execute("UPDATE manual_events SET archived=1, updated_at=? WHERE id=?",
                        (store.now_iso(), event_id)).rowcount:
        raise KeyError(event_id)
    conn.execute("UPDATE events SET active=0, last_changed=? WHERE id=?", (store.now_iso(), f"man:{event_id}"))
    conn.execute("""UPDATE alerts SET status='dismissed' WHERE key LIKE ? AND status != 'dismissed'
                    AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.alert_id = alerts.id)""",
                 (f"experience:man:{event_id}:%",))
    conn.commit()


def sync(conn, event_id: int) -> list[int]:
    """Copy one hand-added event into `events` now and raise (or refresh) its idea. Returns new alert ids."""
    row = conn.execute("SELECT * FROM manual_events WHERE id=?", (event_id,)).fetchone()
    ev = experiences.manual_event(row)
    # protected_prefixes=("",): this batch is one event, so nothing else may be marked "removed"
    store.upsert_events(conn, None, [ev], baseline=False, baseline_prefixes=("man:",), protected_prefixes=("",))
    # upsert keeps old values where the new one is empty; an edit that clears a field must clear it here too
    conn.execute("""UPDATE events SET end_date=?, local_time=?, price_min=?, price_max=?, currency=?, url=?
                    WHERE id=?""", (ev["end_date"], ev["local_time"], ev["price_min"], ev["price_max"],
                                    ev["currency"], ev["url"], ev["id"]))
    conn.commit()
    today = datetime.now(detectors.TZ).date()
    return detectors.save_alerts(conn, detectors.detect_experiences(conn, today, only_ids={ev["id"]}))
