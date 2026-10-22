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
| LLM drafting | `tracker/llm.py`, `tracker/drafts.py`, `tracker/templates.py` | Ollama qwen2.5:3b; presale/status scripts + captions templated, LLM writes hooks/hashtags; on-screen text from facts; `check_facts` tripwire |
| Runner | `tracker/run.py` | `python -m tracker.run [--from-cache] [--no-drafts]` |
| Dashboard API | `tracker/api.py` | FastAPI on 127.0.0.1:8765, serves `dashboard/dist`, one background job at a time |
| Dashboard UI | `dashboard/src/` | React 19 + TS + Vite. Tabs: Alerts & drafts, Events, Runs |
| Web snapshot | `tracker/publish.py`, `dashboard/.env.static` | Read-only Cloudflare Pages copy behind Access; refuses to deploy if the site isn't protected |
| Scheduling | `scripts/install_tasks.ps1`, `uninstall_tasks.ps1` | Installed on the machine (see below) |
| Tests | `tests/` | 77 pytest tests, no network. `tests/factory.py` builds TM-shaped events |

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

- Remote: `github.com/bryanygan/event-tracker`, **public** (the user wants it public). User's rule: skeleton
  went on `main`; new features go on branches. Commit and push as you go once verified; ask before merging.
- `main`: skeleton → pythonw fix → merge of `feature/live-ticketmaster` → squash `9bc0c27` (presale templates)
  → squash `d4bc0e1` (Cloudflare Pages) → merge of `fix/ollama-runner-leak` (`f113735`) → merge of
  `feature/sixers` (`cb0ae9f`, branch deleted). `main` is what the scheduled tasks should run.
- `feature/setlist-history` (pushed, not merged): setlist.fm "first Philly show since …". Ask before merging.
- The templates and Cloudflare branches were **squash-merged and deleted on purpose**: the Cloudflare branch
  had a commit with the account ID and the user's other infra names (see gotchas.md). Don't restore them.
  `feature/live-ticketmaster` still exists (already merged; safe to delete).
- Spotify keys stay in `.env.example` (user wants them kept for possible future use).

## Next up

1. ~~Cloudflare setup~~ Done 2026-10-21: https://philly-events.pages.dev live behind Access (see environment.md).
   Every pipeline run republishes. Confirm the user can log in on their phone.
2. After the 2026-10-22 runs, check that `new_tour` alerts fire on real diffs (they can't fire on a baseline).
3. Brief's phase 5+: setlist.fm for "back after X years" (needs a key), Sixers via TM `classificationName=sports`,
   food/festivals via scrapers, phone notifications, slideshow assets (user has a related project at
   `C:\Users\Admin\Documents\ttspotslideshow`).

## Open questions for the user

- Is Deezer's ranking good enough, or would they get a Last.fm key (listeners + tags) as a second signal?
