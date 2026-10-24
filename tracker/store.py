"""Write normalized events into SQLite and log what changed since the last run."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import config

# Event fields whose change we care about, mapped to the change "kind" we log.
TRACKED_FIELDS = {
    "status": "status",
    "local_date": "date",
    "local_time": "date",
    "price_min": "price",
    "price_max": "price",
    "onsale_start": "onsale",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def venue_tier(name: str | None) -> int:
    lname = (name or "").lower()
    for key, tier in config.VENUE_TIERS.items():
        if key in lname:
            return tier
    return config.DEFAULT_VENUE_TIER


def record_source(conn, run_id: int, source: str, *, ok: bool, count: int, error: str | None = None):
    conn.execute("INSERT OR REPLACE INTO source_runs VALUES (?,?,?,?,?)", (run_id, source, int(ok), count, error))
    conn.commit()


def start_run(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute("INSERT INTO runs (started_at, source) VALUES (?, ?)", (now_iso(), source))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, *, fetched: int, api_calls: int, ok: bool, error: str | None = None):
    conn.execute(
        "UPDATE runs SET finished_at=?, events_fetched=?, api_calls=?, ok=?, error=? WHERE id=?",
        (now_iso(), fetched, api_calls, int(ok), error, run_id),
    )
    conn.commit()


def is_first_run(conn, source: str) -> bool:
    """True if no successful run exists yet. The first run is a baseline: nothing is 'new'."""
    row = conn.execute("SELECT 1 FROM runs WHERE source=? AND ok=1 LIMIT 1", (source,)).fetchone()
    return row is None


def upsert_events(conn: sqlite3.Connection, run_id: int, events: list[dict], *, baseline: bool,
                  baseline_prefixes: tuple[str, ...] = (), protected_prefixes: tuple[str, ...] = (),
                  baseline_ids: frozenset[str] | set[str] = frozenset()) -> dict:
    """Insert/update events and record changes. Returns counts per change kind.

    With baseline=True (first ever run) we store everything but log no 'new' changes,
    otherwise every existing show in Philly would look like a fresh announcement.
    `baseline_prefixes`: the same, for event ids from a source seen for the first time (a new venue calendar).
    `protected_prefixes`: ids from a source that failed this run; don't mark them "removed".
    `baseline_ids`: like baseline_prefixes, for single events (a Ticketmaster feed read for the first time).
    """
    ts = now_iso()
    counts: dict[str, int] = {}

    def log_change(event_id, kind, old=None, new=None):
        conn.execute(
            "INSERT INTO changes (run_id, event_id, kind, old_value, new_value, at) VALUES (?,?,?,?,?,?)",
            (run_id, event_id, kind, None if old is None else str(old), None if new is None else str(new), ts),
        )
        counts[kind] = counts.get(kind, 0) + 1

    seen_ids = []
    for ev in events:
        seen_ids.append(ev["id"])
        v = ev.get("venue")
        if v:
            conn.execute(
                """INSERT INTO venues (id, name, city, state, tier) VALUES (?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, city=excluded.city,
                   state=excluded.state, tier=excluded.tier""",
                (v["id"], v["name"], v["city"], v["state"], venue_tier(v["name"])),
            )
        for a in ev["attractions"]:
            conn.execute(
                """INSERT INTO attractions (id, name, genre, subgenre, tm_upcoming_total, first_seen)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                   genre=COALESCE(excluded.genre, attractions.genre),
                   subgenre=COALESCE(excluded.subgenre, attractions.subgenre),
                   tm_upcoming_total=COALESCE(excluded.tm_upcoming_total, attractions.tm_upcoming_total)""",
                (a["id"], a["name"], a["genre"], a["subgenre"], a["tm_upcoming_total"], ts),
            )

        old = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
        cols = ["name", "url", "image_url", "local_date", "local_time", "status", "segment", "genre",
                "subgenre", "price_min", "price_max", "currency", "onsale_start", "onsale_end", "end_date", "source"]
        values = [ev.get(c) for c in cols]  # end_date/source: only multi-day listings and non-TM sources set them
        venue_id = v["id"] if v else None

        if old is None:
            conn.execute(
                f"""INSERT INTO events (id, {", ".join(cols)}, venue_id, first_seen, last_seen, last_changed, active)
                    VALUES (?, {", ".join("?" * len(cols))}, ?, ?, ?, ?, 1)""",
                [ev["id"], *values, venue_id, ts, ts, ts],
            )
            if not baseline and not (baseline_prefixes and ev["id"].startswith(baseline_prefixes)) \
                    and ev["id"] not in baseline_ids:
                log_change(ev["id"], "new", None, ev["name"])
        else:
            changed = False
            logged_kinds = set()
            for field, kind in TRACKED_FIELDS.items():
                if old[field] != ev[field] and ev[field] is not None:
                    changed = True
                    if kind not in logged_kinds:  # e.g. min+max price move = one 'price' change
                        log_change(ev["id"], kind, old[field], ev[field])
                        logged_kinds.add(kind)
            if not old["active"]:
                log_change(ev["id"], "relisted")
            conn.execute(
                f"""UPDATE events SET {", ".join(f"{c}=COALESCE(?, {c})" for c in cols)},
                    venue_id=?, last_seen=?, active=1, last_changed=CASE WHEN ? THEN ? ELSE last_changed END
                    WHERE id=?""",
                [*values, venue_id, ts, changed, ts, ev["id"]],
            )

        conn.execute("DELETE FROM event_attractions WHERE event_id=?", (ev["id"],))
        for pos, a in enumerate(ev["attractions"]):
            conn.execute("INSERT OR IGNORE INTO event_attractions VALUES (?,?,?)", (ev["id"], a["id"], pos))

        known = {(r["name"], r["start_utc"]) for r in
                 conn.execute("SELECT name, start_utc FROM presales WHERE event_id=?", (ev["id"],))}
        for p in ev["presales"]:
            if (p["name"], p["start_utc"]) not in known:
                conn.execute("INSERT OR IGNORE INTO presales VALUES (?,?,?,?)",
                             (ev["id"], p["name"], p["start_utc"], p["end_utc"]))
                if old is not None and not baseline:
                    log_change(ev["id"], "presale_added", None, p["name"])

    # Events that vanished from the feed (and haven't happened yet) are marked inactive.
    today = datetime.now().date().isoformat()
    if seen_ids:
        placeholders = ",".join("?" * len(seen_ids))
        gone = conn.execute(
            # local_date > today, not >=: tonight's shows drop out of the feeds once they start (TM only
            # returns events that haven't started), which isn't a cancellation. Found 2026-10-21 at 8:37 PM.
            f"SELECT id FROM events WHERE active=1 AND COALESCE(end_date, local_date) > ? AND id NOT IN ({placeholders})",
            [today, *seen_ids],
        ).fetchall()
        for row in gone:
            if protected_prefixes and row["id"].startswith(protected_prefixes):
                continue  # its source failed this run; absence says nothing
            conn.execute("UPDATE events SET active=0, last_changed=? WHERE id=?", (ts, row["id"]))
            log_change(row["id"], "removed")

    conn.commit()
    return counts
