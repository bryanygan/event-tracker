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
