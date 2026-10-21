# Status (as of 2026-10-21)

## What the user wants

A content assistant for a Philly events TikTok page (concerts first; later Sixers, food, festivals).
It tracks events, notices video-worthy moments, and drafts scripts **for review only. Nothing auto-posts.**
The user reviews everything in a local dashboard. They're comfortable with Python/TS/React and like clear,
commented code over abstraction.

## Built (phases 1–4 of the brief + dashboard)

| Piece | File | Notes |
|---|---|---|
| Config + secrets | `tracker/config.py`, `.env` | Radius, thresholds, venue tiers, junk filters all live here |
| SQLite schema | `tracker/db.py` | `runs, venues, attractions, events, event_attractions, presales, changes, alerts, drafts` + `_migrate()` for added columns |
| Ticketmaster fetch | `tracker/fetchers/ticketmaster.py` | Date-window splitting, call budget, 429 retry, name cleaning, junk filter |
| Change tracking | `tracker/store.py` | First run = baseline (no "new"). Logs new/status/date/price/onsale/presale_added/removed/relisted |
| Popularity | `tracker/fetchers/deezer.py` | Deezer `nb_fan` → 0-100 log scale. Replaced Spotify (see decisions) |
| Scoring | `tracker/scoring.py` | 0.5 popularity + 0.3 venue tier + 0.2 tour size; `upcoming_events()` is the shared event view |
| Detectors | `tracker/detectors.py` | new_tour, presale_soon (1 per artist), weekend (Wed+), stacked_month, status_change |
| LLM drafting | `tracker/llm.py`, `tracker/drafts.py` | Ollama qwen2.5:3b; on-screen text built from facts; `check_facts` tripwire |
| Runner | `tracker/run.py` | `python -m tracker.run [--from-cache] [--no-drafts]` |
| Dashboard API | `tracker/api.py` | FastAPI on 127.0.0.1:8765, serves `dashboard/dist`, one background job at a time |
| Dashboard UI | `dashboard/src/` | React 19 + TS + Vite. Tabs: Alerts & drafts, Events, Runs |
| Scheduling | `scripts/install_tasks.ps1`, `uninstall_tasks.ps1` | Installed on the machine (see below) |
| Tests | `tests/` | 40 pytest tests, no network. `tests/factory.py` builds TM-shaped events |

## Live on the machine right now

- Task Scheduler `PhillyEvents-Dashboard`: at logon, `pythonw -m tracker.api`. Running.
- **The scheduled tasks run whatever branch is checked out** in the project folder (they execute the working
  tree). As of the end of session 1 that's `feature/live-ticketmaster`.
- Task Scheduler `PhillyEvents-Pipeline`: daily 6:30 AM + 4:30 PM, `pythonw -m tracker.run`. First scheduled
  run: 2026-10-22 6:30 AM.
- `data/tracker.db`: real baseline from 2026-10-21 (~492 Philly music events, ~412 headliners checked on Deezer).
  `data/cache/tm_last_pull.json` holds the last raw TM pull (replay with `--from-cache`, 0 API calls).
  14 alerts, 10 drafts (8 + 2 spot-check regenerations), none reviewed by the user yet.

## Git

- Remote: `github.com/bryanygan/event-tracker`. User's rule: **skeleton went on `main`; new features go on
  branches.** Commit and push as you go once things are verified.
- `main`: `09e41b9` skeleton, `4f32a3d` pythonw/Task Scheduler fix.
- `feature/live-ticketmaster` (pushed, not merged): consumer-key rename, Deezer, per-artist presale alerts,
  name cleaning, real venue tiers, DB connection closing, stacked-month cutoff, LLM output fixes, agent notes.

## Next up

1. Ask the user whether to merge `feature/live-ticketmaster` into `main` (or open a PR).
2. Draft quality decision pending with the user (see "Open questions").
3. After the 2026-10-22 runs, check that `new_tour` alerts fire on real diffs (they can't fire on a baseline).
4. Brief's phase 5+: setlist.fm for "back after X years" (needs a key), Sixers via TM `classificationName=sports`,
   food/festivals via scrapers, phone notifications, slideshow assets (user has a related project at
   `C:\Users\Admin\Documents\ttspotslideshow`).

## Open questions for the user

- Should the Spotify keys be removed from `.env.example`, or kept for future use (e.g. artist images/links)?
- **Draft quality.** qwen2.5:3b gets facts right now but writes thin scripts and hashtag-only captions,
  and can mix up semantics (read "Eagles Presale" as an Eagles show). Options offered: (a) deterministic
  templates for presale/status scripts with the LLM only doing hooks/captions; (b) qwen2.5:7b (~3 min/draft,
  ~5 GB RAM, watch Homebridge); (c) keep 3B and edit by hand.
- Is Deezer's ranking good enough, or would they get a Last.fm key (listeners + tags) as a second signal?
