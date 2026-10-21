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
