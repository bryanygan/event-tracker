# Status (as of 2026-10-22)

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
| Web snapshot | `tracker/publish.py`, `dashboard/.env.static` | **Retired 2026-10-22** (Pages project + its Access app deleted, PAGES_PROJECT blank); code kept, off |
| Phone editing | `tracker/remote.py` | Live dashboard through a Cloudflare Tunnel hostname; requests need a verified Access login (off until configured) |
| Scheduling | `scripts/install_tasks.ps1`, `uninstall_tasks.ps1` | Installed on the machine (see below) |
| Tests | `tests/` | 96 pytest tests, no network. `tests/factory.py` builds TM-shaped events |

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
- `feature/setlist-history` merged into `main` (`a8435c4`) and deleted.
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

**On hold by the user (2026-10-21): phone notifications** (ntfy etc.). Don't start until the user says the
pipeline is polished and has run for a while without hiccups.

**Venue calendars** (merged into `main`; see venue-coverage.md). The DB holds
~290 `cal:` events from 8 venues. Niche boost + Etix venues were squash-merged into `main` as `1fce53c`
(single squash on purpose: an intermediate commit had real artist names from the user's history in a test).
`main` is checked out.

**Previously: non-Ticketmaster sellers** (AXS, DICE, Tixr…). None has a public API (checked 2026-10-21).
Real gap found: Johnny Brenda's sells via Etix, Underground Arts via Tixr (only some of its shows are also on TM).
Proposed approach: measure which notable venues are missing, then read those venues' own calendars, deduped against TM.

## Creator roadmap progress (see creator-roadmap.md §15)

| Item | State |
|---|---|
| P0-01/02 opportunity data + explainable score | Merged 2026-10-21 (alerts = opportunities; see decisions.md) |
| P0-03 creator-first queue | Merged: "What to post" tab (shortlist / later / note / dismiss, inline draft) |
| P0-04 posts + manual metrics | Merged |
| P0-05 format analytics | Merged (basic): same-age medians, per-1k rates, sample-size flag |
| P0-06 three hooks/angles per idea | Merged 2026-10-22 (`2435acb`): `tracker/angles.py`, no-LLM hooks; templated formats draft with no model |
| P0-07 stale roundup refresh | Merged |
| Web context (not in roadmap) | Merged 2026-10-22: Perplexity lookups on cards, capped and cached |
| P1-08 3–5 pick / genre-cut roundups | Merged 2026-10-22: `picks` on weekend/stacked-month facts + "picks" angle; "your lane this weekend" cut (`weekend_lane:` key) |
| P1-10 genre completeness | Merged 2026-10-22: `tracker/genres.py` (artist genre fill + capped TM name lookup for venue-calendar acts) |
| P1-09 manual trend notes | Merged 2026-10-22: `tracker/trends.py`, `/api/trends`, collapsed panel on "What to post"; fills "search" (rules 2026-10-22.B) |
| P1-11 9:16 slides | Merged 2026-10-22: fact-built manifest (`tracker/slides.py`), canvas renderer, "Slides" overlay |
| P2-15 phone editing | Live 2026-10-22 (`d9b0fac`): REMOTE_HOST route added to the user's existing tunnel (the ttspotslideshow one) + its own Access app ("Admin Only"); user's first 3 phone edits audited ok |
| P1-12 midday check | Live 2026-10-22 (`PhillyEvents-Midday`, 11:30 AM): `--midday`, no LLM/lookups/publish; one-run-at-a-time lock |
| Draft progress bar | Live 2026-10-22: job carries alert_id/step/estimate/elapsed; bar + step under the card's buttons, scroll to the new draft |
| Claude drafts | Live 2026-10-22 (`c90ed9f`) |
| Cleanup | Live 2026-10-22 (`d292940`, `d58a4c1`): Deezer genre check, 20 Claude drafts/run, `--redraft-local` (23 alerts redrafted), genre refresh in live alerts |
| P1-13 editorial calendar | Built 2026-10-22 on `feature/calendar`, NOT merged (user said wait): `tracker/planner.py`, `/api/calendar`, a separate last Calendar tab, no warnings |
| P2-14 | Built on `feature/experience-sources` 2026-10-24 (not merged): more teams, comedy, Center City District + waterfront, events you add by hand, things-to-do roundup. Survey: `experience-sources.md` |
| Music news pillar | `music-roadmap.md`. M0 collecting in gitignored `data/m0/` until Oct 30 (task `PhillyEvents-M0Feeds` deletes itself); user judged 24 stories (summary in roadmap M5). M1 live 2026-10-23 (`PhillyEvents-News`, :10/:40). M2 + M3 live 2026-10-23 (`b11d41c`). M4 catalogs (Deezer + iTunes, New releases view) live 2026-10-23 (`e9730f7`). M5 scoring, feedback buttons, Send to queue live 2026-10-23 (`d7a03d2`). M6 music drafts/roundups/slides/analytics and M7 Discover + momentum live 2026-10-23 (`a11ab00`). M8 (YouTube via the Data API, artists' own channels as official sources, MusicBrainz retry fix, event cards show music stories) live 2026-10-24 (`b9221ec`); key set, first live read took the 3 outlets (6 units). Artist channels fill as the 6:30/16:30 runs resolve links (40 artists per run) |

P0-01..05 and P0-07 merged into `main` 2026-10-21 (user approved); those branches are deleted.
P0-06 merged 2026-10-22 (user approved); branch deleted.

**Web context lookup (Gemini/Perplexity), 2026-10-22:** user approved a capped, cached lookup. Probe result:
the user's Gemini key is free tier; `gemini-2.5-flash` is closed to new keys (404) and Gemini 3+ has no
search grounding on the free tier (429 quota). Needs billing enabled on the key's project (then 5,000
grounded requests/month free) or Perplexity instead. User then enabled billing and asked for a comparison (same dated prompt, 5 queue artists, 10 calls):
Perplexity (`POST /v1/agent`, preset `fast`, ~$0.0016/call, ~4 s, full URLs + publish dates) had no material
errors; Gemini 3.8 Flash (Interactions API, ~15 s, redirect links with domain only) invented a CG5 presale date
and called the on-sale Lizzy McAlpine show sold out. User chose Perplexity; merged 2026-10-22 (`392a5ee`, see decisions.md "Web context"). First auto lookups on the 6:30 AM run. Always put today's
date in the prompt (Gemini searched "2024" without it). Perplexity answer text is in `output[type=message]`,
not `output_text` (null on the fast preset). Google's grounding
terms: show results verbatim with their Search Suggestions, only to the person who asked, so keep them
out of drafts and out of the public snapshot.

## Open questions for the user

- Is Deezer's ranking good enough, or would they get a Last.fm key (listeners + tags) as a second signal?
