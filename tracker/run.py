"""Pipeline entry point: fetch → store/diff → enrich (Deezer) → detect → draft.

Usage (from the project folder):
    .venv\\Scripts\\python -m tracker.run              # full run (what Task Scheduler calls)
    .venv\\Scripts\\python -m tracker.run --no-drafts  # data + alerts only, skip the LLM
    .venv\\Scripts\\python -m tracker.run --from-cache # replay the last raw pull, no API calls
    .venv\\Scripts\\python -m tracker.run --midday     # quick 11:30 check: fetch + detect, no LLM/web lookups

Only one run at a time across processes (scheduled tasks and the dashboard's "Run pipeline now"): a second one
logs that it was skipped and exits.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from contextlib import closing, contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler

from . import config, context, db, detectors, drafts, genres, publish, store
from .fetchers import deezer, setlistfm
from .fetchers import ticketmaster as tm
from .fetchers import venue_calendars

log = logging.getLogger("tracker")
RAW_CACHE = config.CACHE_DIR / "tm_last_pull.json"


def setup_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        # pythonw.exe (Task Scheduler) has no console: libraries that print or call isatty()
        # would crash, so give them a file instead.
        console_log = open(config.LOG_DIR / "console.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or console_log
        sys.stderr = sys.stderr or console_log
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252 and choke on emoji
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file = RotatingFileHandler(config.LOG_DIR / "pipeline.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [file]
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.handlers.append(console)


def fetch_calendars(conn, run_id: int, tm_events: list[dict], from_cache: bool = False):
    """Venue-calendar shows not already on Ticketmaster. Returns (events, first-seen id prefixes,
    failed-source id prefixes) for upsert_events. Each source's outcome is recorded in source_runs."""
    if from_cache:  # replays are for Ticketmaster data; don't hit venue sites, and don't drop their shows
        return [], (), tuple(f"cal:{v['key']}:" for v in config.VENUE_CALENDARS)
    venue_ids = {r["name"].casefold(): r["id"] for r in conn.execute("SELECT id, name FROM venues")}
    out, first_seen, failed = [], [], []
    for venue in config.VENUE_CALENDARS:
        prefix = f"cal:{venue['key']}:"
        venue = {**venue, "venue_id": venue_ids.get(venue["name"].casefold())}  # one venues row per room
        try:
            shows = venue_calendars.fetch_venue(venue)
        except venue_calendars.SourceError as exc:
            log.warning("Venue calendar %s failed: %s", venue["name"], exc)
            store.record_source(conn, run_id, venue["key"], ok=False, count=0, error=str(exc))
            failed.append(prefix)
            continue
        kept, dupes = venue_calendars.drop_duplicates(shows, tm_events)
        if not conn.execute("SELECT 1 FROM events WHERE id LIKE ? LIMIT 1", (prefix + "%",)).fetchone():
            first_seen.append(prefix)  # first read of this venue: its shows aren't "just announced"
        store.record_source(conn, run_id, venue["key"], ok=True, count=len(kept))
        log.info("Venue calendar %s: %d shows, %d already on Ticketmaster", venue["name"], len(shows), dupes)
        out += kept
    return out, tuple(first_seen), tuple(failed)


class RunBusy(Exception):
    pass


@contextmanager
def single_run():
    """Hold an OS file lock for the whole run. The OS drops it if the process dies, so it never goes stale."""
    config.RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(config.RUN_LOCK, "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RunBusy("another pipeline run is in progress") from None
        yield
    finally:
        f.close()  # closing the handle releases the lock


def run(from_cache: bool = False, make_drafts: bool = True, conn=None, midday: bool = False) -> dict:
    """One pipeline run. `midday`: the quick late-morning check for tours and presales announced after the
    6:30 run. Same fetch and detectors, but no drafting (Ollama never starts), no paid web lookups, and only a
    few setlist.fm history checks (new artists first), so it takes about a minute."""
    if conn is None:
        with single_run(), closing(db.connect()) as own:
            return run(from_cache, make_drafts and not midday, own, midday)
    make_drafts = make_drafts and not midday
    baseline = store.is_first_run(conn, "ticketmaster")
    run_id = store.start_run(conn, "ticketmaster")
    calls = 0
    try:
        if from_cache:
            raw = json.loads(RAW_CACHE.read_text(encoding="utf-8"))
        else:
            client = tm.TicketmasterClient()
            raw = client.fetch_events()
            for classification in config.SPORTS_CLASSIFICATIONS:
                raw += [r for r in client.fetch_events(classification) if tm.is_tracked_team_event(r)]
            calls = client.calls
            tm.save_raw(raw, RAW_CACHE)
        events = [tm.normalize(r) for r in raw if not tm.is_junk(r)]
        log.info("Fetched %d raw events, %d after filtering (%d API calls)%s",
                 len(raw), len(events), calls, " [baseline run]" if baseline else "")
        calendar, first_seen, failed = fetch_calendars(conn, run_id, events, from_cache=from_cache)
        counts = store.upsert_events(conn, run_id, events + calendar, baseline=baseline,
                                     baseline_prefixes=first_seen, protected_prefixes=failed)
        log.info("Changes: %s", counts or "none")

        if not from_cache:  # genres for venue-calendar acts, from Ticketmaster's attraction search (capped)
            try:
                genres.fill_from_ticketmaster(conn, client)
            except Exception as exc:  # never fail the run over a nice-to-have
                log.warning("Genre lookups failed: %s", exc)
            calls = client.calls

        try:
            deezer.enrich_headliners(conn)
        except Exception as exc:  # enrichment is a nice-to-have; never fail the run over it
            log.warning("Deezer enrichment failed: %s", exc)
        try:
            setlistfm.enrich_history(conn, limit=config.MIDDAY_SETLIST_ARTISTS if midday else setlistfm.MAX_ARTISTS_PER_RUN)
        except Exception as exc:
            log.warning("setlist.fm history failed: %s", exc)

        alerts = detectors.run_all(conn, run_id)
        new_alert_ids = detectors.save_alerts(conn, alerts)
        log.info("Detectors raised %d alerts, %d new", len(alerts), len(new_alert_ids))
        store.finish_run(conn, run_id, fetched=len(events), api_calls=calls, ok=True)
    except Exception as exc:
        log.exception("Run failed")
        store.finish_run(conn, run_id, fetched=0, api_calls=calls, ok=False, error=str(exc))
        raise

    if not (from_cache or midday):  # replays don't spend money; the caps in context.py keep real runs to cents a month
        try:
            looked_up = context.auto_lookup(conn)
            if looked_up:
                log.info("Looked up web context for %d ideas", looked_up)
        except Exception as exc:
            log.warning("Web context lookups failed: %s", exc)

    made = []
    if make_drafts:
        try:
            made = drafts.draft_pending(conn)
            log.info("Generated %d drafts", len(made))
        except Exception as exc:
            log.exception("Drafting failed: %s", exc)  # data is saved; drafts can be retried from the dashboard
    if not midday:
        published = publish.publish(conn)  # no-op unless Cloudflare Pages is configured; never raises
        log.info("Web snapshot: %s", published)
    return {"run_id": run_id, "events": len(events), "changes": counts, "new_alerts": len(new_alert_ids),
            "drafts": len(made)}


def main(argv=None):
    p = argparse.ArgumentParser(description="Philly events pipeline")
    p.add_argument("--from-cache", action="store_true", help="replay last raw Ticketmaster pull (no API calls)")
    p.add_argument("--no-drafts", action="store_true", help="skip LLM drafting")
    p.add_argument("--midday", action="store_true", help="quick check: no drafts, web lookups or publish")
    p.add_argument("--redraft-local", action="store_true",
                   help="one-off: new Claude drafts for live alerts whose newest draft came from Ollama")
    args = p.parse_args(argv)
    setup_logging()
    if args.redraft_local:
        with closing(db.connect()) as conn:
            made = drafts.redraft_local(conn)
        log.info("Redrafted %d alerts with Claude", len(made))
        return
    started = datetime.now()
    try:
        result = run(from_cache=args.from_cache, make_drafts=not args.no_drafts, midday=args.midday)
    except RunBusy as exc:
        log.info("Skipped this %srun: %s", "midday " if args.midday else "", exc)
        return
    log.info("Done in %ss%s: %s", (datetime.now() - started).seconds, " (midday)" if args.midday else "", result)


if __name__ == "__main__":
    main()
