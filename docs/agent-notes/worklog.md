# Work log

Newest session at the bottom. Keep entries short: what was done, what was found, what's left.

## 2026-10-21: Session 1 (Claude Code, Opus 5.5)

### Kickoff
- Read the brief. Checked the machine: N95/12 GB/no GPU, Python 3.13, Node 24, Ollama installed but off with
  only `llama3.2:1b`.
- Asked the user 4 questions. Answers: has Ticketmaster + Spotify keys; use qwen2.5:3b (or llama3.2:3b);
  **dashboard from the start**; scope = phases 1–4.

### Built the skeleton (all against fake TM-shaped data while keys were pending)
- Config, schema, TM fetcher (window splitting, budget, 429 retry), store with change log, scoring, detectors,
  LLM module, drafts, runner, FastAPI, React dashboard, Task Scheduler scripts, README.
- Benchmarked qwen2.5:3b: ~6.5 tok/s. First drafts were flat and ignored "no emoji in script" → added one
  example per format, `spoken()` post-processing, placeholder handling. Compared with llama3.2:3b → kept qwen.
- Bugs found and fixed along the way: control-byte corruption from heredoc escapes (twice), price rounding
  ($59.50→$60), month-name normalization in `check_facts`, favicon served as index.html by the SPA route,
  " !" spacing after emoji removal.
- Verified: cold-start Ollama → draft → Ollama stopped with no leftovers; dashboard draft job end to end.

### User: install tasks, commit + push (main for skeleton, branches for features)
- Committed `09e41b9` to `main` and pushed.
- Installed tasks. The dashboard task exited 1: `pythonw` has no stdout, uvicorn calls `isatty()`. Reproduced
  with `Start-Process`, fixed in `setup_logging`, committed `4f32a3d` to `main`.
- Stopped the Ollama + tray app processes that an early `ollama list` had launched.

### Live data (branch `feature/live-ticketmaster`)
- User renamed keys to `TICKETMASTER_CONSUMER_KEY/SECRET` (their first save didn't land; it was unsaved in
  the editor).
- One probe call: 502 events, 15% with prices, 37% with presales, branded venue names, age-limit name noise.
- First real run (3 TM calls): 492 events, but **53 alerts** (47 presale) and **Spotify returned no
  popularity/genres** for any artist.
- Fixes: Deezer replaces Spotify; one presale alert per artist with all windows, ignore VIP/package;
  `PRESALE_MIN_SCORE`; name cleaning; venue tiers from real venue list; Deezer empty-name/error handling;
  API closes DB connections; deterministic sale ordering; stacked-month skips current month after the 15th.
- Rebuilt `data/tracker.db` from cache (nothing had been reviewed yet). Result: 15 alerts (11 presale,
  2 stacked month, 1 weekend…), sensible ranking (Calvin Harris, Olivia Rodrigo, Doja Cat at top).
- Created these agent notes at the user's request.
- Reviewed the first 8 real drafts: caption copied an internal `note` from facts, wrong weekday for a date,
  `[Artist]` leak, hashtag typos, "tickets are live now" before the sale, codes mentioned for a general on-sale.
  Fixed: note removed from facts; weekday+date check; case-insensitive placeholder fill; `clean_hashtags`;
  presale guide rules; pronoun rule.
- Second regeneration: dates right, but the model mixed up show date vs sale date for Zach Bryan ("Eagles
  Presale" read as an Eagles show). Fixed: prompt shows `show_date`/`show_time`; drafts with warnings are
  retried once. Spot-check after: both problem drafts correct, but scripts are thin and captions are mostly
  hashtags. That's likely the 3B model's ceiling; options put to the user.
- Committed and pushed `feature/live-ticketmaster`; restarted the dashboard task.

### User: merge, templates (option a), keep Spotify keys, Cloudflare Pages site
- Merged `feature/live-ticketmaster` → `main` (`7460200`).
- `feature/presale-templates`: presale/status scripts and captions templated, LLM writes hooks + hashtags.
  Regenerated all 11 presale drafts: all accurate, 0 warnings, ~45 s each. Follow-up: hook must contain the exact
  artist name (one said "THROW" for THROWN), venue hashtags snapped too. Not merged yet.
- Spotify keys kept in `.env.example` at the user's request (unused).
- Asked what "Cloudflare Pages website" meant → **private, read-only dashboard snapshot**; user has a Cloudflare
  account but no domain, tunnel or Zero Trust yet.
- `feature/cloudflare-pages` (built on the templates branch): static build mode, `tracker/publish.py`, Access
  guardrail, publish after each run + button, `tests/conftest.py`. Verified export (14 alerts, 492 events,
  ~600 KB) and served it locally. **Not deployed yet**: waiting on the user's API token, account ID, project
  name and Zero Trust setup.
- Cloudflare: user added token/account ID and `PAGES_PROJECT=philly-events`. Created the project via API
  (wrangler's create silently did nothing), fixed `site_url()` to read the real subdomain, created an Access app
  reusing the user's "Admin Only" policy, deployed a data-free placeholder to activate DNS, verified main +
  wildcard URLs redirect to Access, extended the guard to check a made-up subdomain, then published the real
  snapshot. Verified `/data/*.json` and the deployment URL also require login.
- Repo is public (user confirmed they want that). Squash-merged templates + Cloudflare branches into `main` as
  `9bc0c27` and `d4bc0e1`, scanned `main` history for the leaked strings (none), deleted both branches locally
  and on GitHub. No ref reaches the leaked commit `6927b43` any more.

### Sixers + setlist.fm (same day, evening)
- `feature/sixers`: TM basketball feed (103 events: Sixers, Villanova, Blue Coats, NCAA) filtered to Sixers home
  games (40); game scoring; `team_week` + `big_game` templated formats. Verified on real data.
- A live full run was **killed by Claude Code for low system memory** during drafting. Cause: 8 orphaned Ollama
  `llama-server.exe` runners (one per drafting session today), the newest holding 2.2 GB. Killed them
  (free RAM 2.2 → 5.4 GB), fixed `ollama_session` (tree kill + orphan sweep) on `fix/ollama-runner-leak`, verified
  live that nothing remains, merged to `main`, merged `main` into `feature/sixers`. Did not restart the run.
- setlist.fm key received (`SETLISTFM_API_KEY`). Probed: cityName search is fuzzy (Camden matches), so the plan
  uses venue coords within the radius. `tracker/fetchers/setlistfm.py` written, not wired in or committed yet.
- User approved drafting the Sixers alerts and merging `feature/sixers`. Drafted (cleanup verified: 0 runners
  left, RAM unchanged). Fixes from review: big-game hooks may name the opponent, templated hooks ≤ 12 words,
  game hashtags templated (model gave #seahawksvsjets, #ClevelandVikings). Merged to `main` (`cb0ae9f`).
- `feature/setlist-history`: schema columns + migration, `enrich_history` in the run, history facts on presale and
  new-tour alerts, comeback script/caption/on-screen lines, verify warning, setlist.fm credit in captions.
  Switched lookup from "page last 100 setlists" (20/40 found) to artist+city search + recent page (90/100).
  Live: Papa Roach (Met, Aug 2019) and Calvin Harris (Wells Fargo, Dec 2018) presales got the comeback angle;
  refreshed those two alerts' facts and redrafted them (other presale alerts kept their original facts).
- User confirmed Calvin Harris's Dec 2018 Philly date was Q102 Jingle Ball (15 acts). Added lineup-size detection
  (setlists at venue+date; ≥5 = multi-act) and "Last time was a 9-act lineup" wording. Backfilled 90 artists
  (279 calls; ~760 setlist.fm calls used today). 9 artists' last Philly show was multi-act.
- Merged setlist-history to `main`. User put phone alerts on hold until things are polished and stable.
- Other sellers: researched (no public APIs), surveyed 28 venue sites, wrote `venue-coverage.md`.
- `feature/venue-calendars`: JSON-LD reader for 5 venues, TM dedupe, per-source baseline, failed-source
  protection, `source_runs` + dashboard warning, junk filter, status markers (sold out/cancelled), UTC→ET.
  Live run: +177 shows (704 total in the snapshot), no fake "new" flood. First real new-show alert: Lizzy McAlpine
  at Xfinity Mobile Arena. Found and fixed the "tonight's shows marked removed" bug (undid 6 bogus removals).
- Merged venue calendars. `feature/niche`: listening-history boost (see decisions.md). Live: rotation artists moved
  up (e.g. a 48 → 73), in-lane stacked-month alerts appeared for Pop and Dance/Electronic.
- `feature/etix-venues` (stacked on `feature/niche`): rhp + PhilaMOCA HTML parsers, +71 shows, film/wrestling
  filter. Live run: 8/8 venues read, 781 events, no fake "new", no bogus removals.

### Creator roadmap, first pass (2026-10-21, late evening)
- User added `creator-roadmap.md` (committed on `main`). Worked in a git worktree (`../events-tracker-dev`) so
  `main` stayed checked out for the scheduled tasks; tested against a *copy* of the real DB on port 8766.
- `feature/post-tracking` (P0-04/05): posts + metric snapshots, same-age comparisons, "Posted" tab, "Log as posted".
- `feature/alert-refresh` (P0-07, stacked): alerts refresh facts, history kept, drafts show what changed. On real
  data 5 alerts would refresh (weekend gained Arcángel + 2 Sixers games; Nov Hip-Hop 6 → 9; 3 presales got
  setlist.fm history).
- `feature/content-queue` (P0-01/02/03, stacked): opportunity score, "What to post" default tab. Checked with
  headless-Edge screenshots, which exposed the thread/close 500 (fixed on `fix/db-thread` from `main`, cherry-
  picked into the stack). None merged yet: waiting for the user.

### Angles merged, web context (2026-10-22, early morning)
- Merged `feature/angles` (user approved), rebuilt, restarted the dashboard; 21/21 cards show angles.
- Gemini vs Perplexity: probed Gemini (free tier had no grounding; user enabled billing), then 10-call comparison.
  Perplexity won on accuracy and sources (details in decisions.md).
- `feature/web-context`: `tracker/context.py`, `web_context` table, `POST /api/alerts/{id}/context`,
  `/api/context/usage`, pipeline auto step, card section + queue hint, tabs in the URL hash (`#alerts`).
  Real-data check on the DB copy: 1 manual + 3 auto lookups ($0.0063 total), cache reuse confirmed.

### P1-08 + P1-10 (2026-10-22, agent A, `feature/roundups-genres`)
- `tracker/genres.py`: read-time genre fill + capped TM attraction name lookup. Real-DB copy: +59 TM events get
  the artist's genre; 40 real TM calls matched 18 venue-calendar acts (spot-checked: High On Fire = Metal,
  Mark Guiliana = Jazz, June McDoom = Folk, YHWH Nailgun = Alternative, MAX STYLER = Dance/Electronic).
- Picks + lane weekend cut + "picks" angle. On the copy: a Metal stacked month appears (5 shows, thanks to filled
  genres), Hip-Hop goes to 11, and "Your lane this weekend: Pop, EDM & Hip-Hop (8 shows)" is raised.
  Heads-up: on the first real run the filled genres change 5 existing roundups' shows, so their drafts show
  "facts changed" (a real change, not a bug).

### P1-09 trend notes (2026-10-22, agent B on `feature/trend-notes`)
- `tracker/trends.py`, `trend_notes` table, `/api/trends` CRUD, queue "search" component, snapshot `trends.json`,
  `TrendNotes.tsx` panel + card pills. 20 new tests (165 total). Real-data check on a DB copy (port 8768):
  a national artist note, a Philly genre note and an expired note matched the right 7 ideas; the other 14 kept
  "search" unknown. Screenshots at desktop and ~420px width.

### P1-11 slides (2026-10-22, build agent C, `feature/slides`)
- `tracker/slides.py` + `slide_decks` table + `/api/alerts/{id}/slides` (GET/PUT/DELETE, POST rebuild);
  `slides.json` in the static snapshot; canvas renderer + full-screen overlay opened from a "Slides" button.
- Checked on a copy of the real DB: all 7 formats build; rendered presale, weekend (8 shows) and team-week
  decks; stress test (95-char name, unbreakable word, 7 sale lines, photo); date-correction rebuild kept the
  artist edit and photo; static snapshot renders read-only at 500px.

### P1-08..11 integrated (2026-10-22, early morning)
- Three build agents in parallel worktrees (`../et-roundups`, `../et-trends`, `../et-slides`), combined on
  `integration/p1`, then a security review agent and a functional/mobile QA agent. Fixed everything they found
  (see decisions.md "Integration review fixes"). 226 tests. Real-data re-detect on a DB copy: picks only where
  they're a real cut, lane slides headline "Pop, EDM & Hip-Hop this weekend", weekend deck keeps a music theme,
  EDM/Hip-Hop notes match. Phone screenshots at 500px: cards ~1/5 screen with angles collapsed.

### 2026-10-22 midday: run 8 review, phone editing
- Run 8 (6:30 AM) ok in 569 s: 4 TM calls, 3 new venue-calendar shows, genre step 18 found / 18 none / 4
  ambiguous, 11 alerts refreshed, 2 new (Metal month, lane cut), 4 auto web lookups ($0.0063), 6 drafts,
  snapshot published (slides.json 76 KB).
- Logged the user's Calvin Harris video (post 1, alert 9; no link yet).
- P2-15 phone editing on `feature/remote-editing` (see decisions.md "Phone editing"). 15 new tests (241).
- Merged (`d9b0fac`), Access app created, `.env` filled, dashboard restarted. User added the hostname to their
  existing tunnel (new dashboard has one URL field: `http://127.0.0.1:8765`). Unauthenticated page/API/POST
  and a forged token all 302 to the Access login; the user's 3 phone edits (dismiss, note, shortlist) logged.
  Pi-hole cached the hostname's NXDOMAIN from before the route existed; it clears on its own.

### 2026-10-22 afternoon: 2hollis miss, midday check, Pages retired
- 2hollis (Fillmore, Feb 16 2027, public on-sale Oct 28 10 AM ET, presales Oct 27) was published on TM after the
  6:30 run; the area query returns it by 4 PM. Built `--midday` (P1-12) on `feature/midday`, 3 new tests (244).
- Deleted the Pages project and its Access app; PAGES_PROJECT blanked in `.env`.
- 4:30 run (run 9, 453 s): 2hollis ingested, alert 24 (new_tour, score 88) drafted. Publish skipped as intended.
- Merged `feature/midday` (`4796005`), registered `PhillyEvents-Midday` (first run Oct 23 11:30 AM), rebuilt and
  restarted the dashboard.
- Draft progress bar on `feature/draft-progress` (user asked for visual confirmation on "New draft"). Also fixed
  the latest DraftView not being keyed by draft id, so its text could stay on the old draft after a new one loaded.
  `llm._ollama_up` is now public `ollama_up`. 3 new tests (247).
- Merged `feature/draft-progress` (`6cb1523`), dashboard restarted.
- `feature/claude-drafts`: Claude Haiku drafting with Ollama fallback, daily cap and cost log (decisions.md); a
  "✓ Posted" button on queue cards (the only "Log as posted" was inside the opened draft). 5 new tests (252).
- Claude trial on a DB copy (see decisions.md); CLAUDE_EFFORT=medium, HOUSE_TAGS typo fix, batch fallback. 254 tests.
- `feature/cleanup`: Deezer release genres check Ticketmaster's (decisions.md), 20 drafts per run with Claude,
  `--redraft-local` for the old Ollama drafts. 8 new tests (262).
- Merged `feature/cleanup` (`d292940`); `--from-cache` run backfilled Deezer genres for 600 artists (the rest on
  the next run); `new_tour` alerts never refresh, so `feature/genre-refresh` (`d58a4c1`) updates show genres in
  live alerts each run (posted alerts excluded, e.g. 2hollis). `--redraft-local`: 23 Claude drafts, 0 fact
  warnings, $0.007 this month.
- `feature/calendar` (P1-13): planner.py, `/api/calendar`, Calendar tab, series on posts. User asked to keep it
  separate from the other tabs and drop the busy-day/mix/repeat warnings (posting 2-3 a day is fine; plans are a
  shortlist). Not merged yet (user's call). 6 new tests (269).

### 2026-10-23 early morning: music pillar M0 and M1
- Roadmap merged (`041020b`). M0 coverage prototype collecting into gitignored `data/m0/` (14 feeds, every 30 min
  until Oct 30, `PhillyEvents-M0Feeds`). First report: 46 watchlist articles → 34 stories, 25 of 335 core artists
  in a headline, 0 of 17 Friday releases in feeds yet. User marked 24 stories Y/N; lessons in roadmap M5.
- `feature/news-rss` (M1): `news_sources` + `articles` tables, `tracker/news.py` (conditional GETs, per-source
  back-off 30/60/120 min, pipeline lock), `/api/news`, `/api/news/sources`, Music news tab (last). News runs kept
  out of the Runs tab and the header's last run. Live read into a scratch DB: 343 articles, 0 failures, 15 s.
  14 new tests (283).
- Merged `feature/news-rss` (`202b66c`), registered `PhillyEvents-News` (no end date), dashboard rebuilt and
  restarted. First scheduled check: 14 feeds, 343 articles, 15 s.
- `feature/watchlist` (M2): `artist_identities` + `article_artists`, seeding from listening (names/tiers only),
  matching with the tightened common-word rule, Deezer + MusicBrainz ids (40/run), `/api/watchlist`, For you
  filter, artist pills and a Watchlist editor in the Music news tab. 14 new tests (297).
- `feature/stories` (M3, stacked on M2): `stories`, `story_articles`, `story_evidence`; rule-based types (incl. the
  user's M0 kinds: legal, politics, brand/media, lifestyle, roundup), release status and dates, conflicting dates,
  clustering with a shared-word check, `/api/news/stories`, Stories view (default) in Music news. Checked with
  headless Edge screenshots on a DB copy. 30 new tests (327).
- Merged M2 + M3 (`b11d41c`), dashboard restarted; first news check built 335 artists and 29 stories live.
- M4 bake-off (22 artists; decisions.md): kept Deezer + iTunes Search, MusicBrainz for ids only. `feature/catalog`:
  `releases`, catalog polling in the news job (16 artists/check), catalog evidence (scheduled/released, confirmed or
  conflicting), catalog-only stories in a New releases view, guest spots as "featured on". 15 new tests (341).
- Merged `feature/catalog` (`e9730f7`), dashboard restarted, live DB migrated (catalogs fill from the next checks).
- `feature/music-scoring` (M5): `tracker/music.py` (story score, feedback, Send to queue, auto-send off),
  `story_actions`, `seen_live`; For you / New releases / All headlines with score pills, angle, reasons, caveats
  and the buttons; queue pillar filter and music path in `opportunities.py`. On a live-DB copy the top 6 stories
  were all ones the user had marked Y in M0. 13 new tests (354).
- Merged `feature/music-scoring` (`d7a03d2`), dashboard restarted; live DB got `story_actions` and `seen_live`.
- `feature/music-drafts` (M6): `tracker/music_formats.py` (formats, status phrases, YOUR TAKE line, music fact
  checks, New Music Friday + upcoming-albums roundups), music paths in llm/drafts/slides/context/posts, dashboard
  drafting + slides for music ideas. 8 real Haiku drafts on a DB copy, all clean. 9 new tests (363).
- `feature/music-discovery` (M7, stacked): `tracker/discovery.py`, Last.fm + ListenBrainz clients, Deezer fans,
  candidates/metrics/cache tables, momentum rules, Discover view with accept/dismiss and "Artists to watch" ideas.
  Real refresh on a DB copy: 150 candidates in 11 s. 8 new tests (371).
- Merged M6 + M7 (`a11ab00`); 371 pass on main.

## 2026-10-24

- `fix/draft-refresh`: a draft that finished between two 3 s job polls showed "New draft ready" but the old
  draft until a page reload (App only refreshed after a poll had seen the job running). App now refreshes when
  the finished job changes (started|finished). Reproduced and verified by clicking "Draft this" in headless Edge
  over CDP on a DB copy (old build: draft #82 stayed; new: #84 appeared at the "ready" note).
- Merged `fix/draft-refresh` (`8423d40`).
- `feature/music-sources` (M8): MusicBrainz 503 retries + url-rels links; `fetchers/youtube.py` (Data API, off
  without YOUTUBE_API_KEY) for outlet channels and artists' own channels (synced from links); owner tagging,
  YouTube classification and `confirmed` official evidence in stories; event cards show the acts' recent music
  stories. conftest now blanks LAST_FM_API_KEY / YOUTUBE_*: the suite had been calling Last.fm, Deezer and
  ListenBrainz live since M7 (389 tests now run in 13 s instead of ~4 min). 18 new tests (389).
- Merged M8 (`b9221ec`); YOUTUBE_API_KEY added by the user, first live read took the 3 outlets (6 units).
- P2-14 survey (`experience-sources.md`, 36 sites), then `feature/experience-sources`: Flyers/Eagles/Phillies/Union
  (per-team classification, rivals, league tags, emoji), stand-up from TM's comedy feed, Center City District
  (Events Calendar API) and Delaware River Waterfront (iCal) as "Experience" events, events you add by hand
  (`manual_events`, Events tab form), `experience` and `things_to_do` ideas, "free"/"cheap" fact checks. A feed's
  first read is a baseline (`new_feed_ids`): the first real run otherwise raised 31 "just announced" ideas for
  comedians already on sale. Real run on a DB copy: 164 comedy shows, 84 new games, 18 experiences, 5 new ideas;
  two Haiku drafts clean. Built in a git worktree (`../events-tracker-p214`) so the main folder stayed on `main`.
  27 new tests (415).
- Merged P2-14 (`b8a4529`). The 4:30 PM run started 15 min before the merge, so it ran the old code; a preview
  run from `main` on a DB copy matched the branch run (10 TM calls, 164 comedy, 18 local, 5 ideas). One fix: an
  experience title repeated the name as its venue ("Open Streets: West Walnut … at Open Streets: West Walnut").
- `feature/health-guards`: Ollama low-memory guard (`llm.check_memory`, `OLLAMA_MIN_FREE_MB` 3500 via
  GlobalMemoryStatusEx, no new dependency; skipped when the model is already loaded). A short batch doesn't
  crash or retry the start per draft: templated formats draft from their template, the rest stay "new". Source
  health (`tracker/health.py`, `/api/health`, a Sources table on the Runs tab; the banner now covers every
  source plus stopped scheduled runs), read only from runs/source_runs/news_sources. 11 new tests (426).
