"""Pipeline entry point: fetch → store/diff → enrich (Deezer) → detect → draft.

Usage (from the project folder):
    .venv\\Scripts\\python -m tracker.run              # full run (what Task Scheduler calls)
    .venv\\Scripts\\python -m tracker.run --no-drafts  # data + alerts only, skip the LLM
    .venv\\Scripts\\python -m tracker.run --from-cache # replay the last raw pull, no API calls
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import closing
from datetime import datetime
from logging.handlers import RotatingFileHandler

from . import config, db, detectors, drafts, publish, store
from .fetchers import deezer, setlistfm
from .fetchers import ticketmaster as tm

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


def run(from_cache: bool = False, make_drafts: bool = True, conn=None) -> dict:
    if conn is None:
        with closing(db.connect()) as own:
            return run(from_cache, make_drafts, own)
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
        counts = store.upsert_events(conn, run_id, events, baseline=baseline)
        log.info("Changes: %s", counts or "none")

        try:
            deezer.enrich_headliners(conn)
        except Exception as exc:  # enrichment is a nice-to-have; never fail the run over it
            log.warning("Deezer enrichment failed: %s", exc)
        try:
            setlistfm.enrich_history(conn)
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

    made = []
    if make_drafts:
        try:
            made = drafts.draft_pending(conn)
            log.info("Generated %d drafts", len(made))
        except Exception as exc:
            log.exception("Drafting failed: %s", exc)  # data is saved; drafts can be retried from the dashboard
    published = publish.publish(conn)  # no-op unless Cloudflare Pages is configured; never raises
    log.info("Web snapshot: %s", published)
    return {"run_id": run_id, "events": len(events), "changes": counts, "new_alerts": len(new_alert_ids),
            "drafts": len(made)}


def main(argv=None):
    p = argparse.ArgumentParser(description="Philly events pipeline")
    p.add_argument("--from-cache", action="store_true", help="replay last raw Ticketmaster pull (no API calls)")
    p.add_argument("--no-drafts", action="store_true", help="skip LLM drafting")
    args = p.parse_args(argv)
    setup_logging()
    started = datetime.now()
    result = run(from_cache=args.from_cache, make_drafts=not args.no_drafts)
    log.info("Done in %ss: %s", (datetime.now() - started).seconds, result)


if __name__ == "__main__":
    main()
