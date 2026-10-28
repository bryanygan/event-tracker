# Gotchas and mistakes already made

## Writing code from the shell

- **Bash heredocs + Python string escapes corrupt regexes.** Writing `"\\b"` inside a quoted heredoc fed to
  Python, which then wrote a normal string, produced literal backspace (`\x08`), `\x01`, `\x02` bytes in
  `tracker/llm.py` and later `tracker/config.py`. The code still imported; the regexes just silently didn't match.
  `sed -n` output hides these bytes (`cat -A` shows `^H`). **Use the Edit/Write tools for anything containing
  regex backslashes.** Check with `grep -lP '[\x01-\x08]' tracker/*.py tests/*.py`.
- `sed -i` with `\n` and backslashes is just as fragile; prefer Edit.
- Same trap, third time (notes update): a Windows path like `C:\Users\...` inside a Python `"""..."""` string in
  a heredoc is a `\U` unicode escape, so the script fails with a SyntaxError. Even for docs, use Edit.
- Fourth time (M6/M7, 2026-10-23): Python-heredoc "replace" scripts turned `"\\n"` into a real line break inside a
  string literal (SyntaxError in llm.py and slides.py) and `\\b` into `\x08` in discovery.py. A plain-text replace
  script is only safe when neither the old nor the new text contains a backslash.

## Windows / Task Scheduler

- **`pythonw.exe` has `sys.stdout`/`sys.stderr` = None.** Uvicorn's log formatter calls `isatty()` on them and
  crashes, so the dashboard task exited with code 1 and wrote no log. Fixed in `run.setup_logging()`: missing
  streams are pointed at `logs/console.log`, and uvicorn runs with `log_config=None`.
- **Testing `pythonw` from Git Bash does NOT reproduce that:** the shell hands it pipes, so stdout isn't None.
  Reproduce with PowerShell `Start-Process .venv\Scripts\pythonw.exe -ArgumentList '-m','tracker.api' -PassThru`
  and check `ExitCode`.
- Task Scheduler `LastTaskResult` 267009 = currently running, 267011 = never run yet, 1 = the process exited 1.
- Windows console is cp1252: printing emoji from drafts raises `UnicodeEncodeError`. `setup_logging()`
  reconfigures streams to UTF-8; ad-hoc scripts need `PYTHONIOENCODING=utf-8`.
- Python on Windows has no IANA tz database: `tzdata` must be installed (it's in requirements.txt).
- `grep` treats output containing emoji as binary and truncates it ("Binary file (standard input) matches").
  Use `grep -a`. This once made a working weekend alert look missing.
- **Open SQLite connections lock the DB file.** The API originally opened a connection per request and never
  closed it. Stop the dashboard task before moving/deleting `data/tracker.db`.
- **Low-memory guard (2026-10-24).** `ollama_session` raises `llm.LowMemory` before starting anything when
  free RAM < `OLLAMA_MIN_FREE_MB` (3500; the PC has 12 GB, the model takes ~2.2 GB). `model_session` stores it
  in `_batch["low_memory"]` so later drafts in the batch fail fast; drafts.py turns it into a template draft for
  templated formats. Tests that need Ollama "up" must also stub `free_memory_mb` or `check_memory`.
- `ollama list` with the server down can hang and/or launch the Ollama tray app (`ollama app.exe`). The user
  keeps Ollama off; if you start it, stop both `ollama` and `ollama app` afterwards.
- **Memory leak, found 2026-10-21 (my earlier "no leftovers" check was wrong).** Ollama 0.32's model runner is
  `lib\ollama\llama-server.exe` (older versions: `ollama_llama_server.exe`). `ollama_session` killed only
  `ollama serve` plus the *old* runner name, and my checks only matched `ollama*`, so every drafting session left
  a runner behind. 8 accumulated in one afternoon; the newest held 2.2 GB, and Claude Code killed a pipeline run
  for low memory. Fix: kill by process tree (`taskkill /F /T /PID`), and sweep orphaned runners before starting
  Ollama (orphans appear when the pipeline itself is killed mid-run). **When checking for leftovers, match
  `ollama|llama-server`**, and check free RAM (`Win32_OperatingSystem.FreePhysicalMemory`).

- **FastAPI may tear down a sync dependency on a different thread** than the one that ran its setup, and sqlite3
  refused `close()` there: random 500s when the browser loads several endpoints at once. Tests didn't catch it
  (TestClient requests are sequential). Fixed with `check_same_thread=False` in `db.connect` (2026-10-21).
- **Headless Edge lays pages out at least ~492px wide** (`--window-size=412,...` still gives `innerWidth=492`) and
  crops the screenshot, so phone-width screenshots look cut off on the right. Not a page bug. Screenshots:
  `msedge --headless=new --user-data-dir=<scratch> --window-size=1200,2400 --virtual-time-budget=8000 --screenshot=<png> <url>`.

## Data assumptions that were wrong

- Assumed Spotify would give popularity/genres. It doesn't anymore (see decisions.md). Check API responses
  with one live call before building on them.
- Assumed one alert per presale window was fine. Real data: 4–5 windows per show → alert flood.
- Assumed venue names like "The Met Philadelphia". Real: "The Met Presented by Highmark". Always pull the real
  venue list (`SELECT v.name, COUNT(*) FROM events JOIN venues ...`) before tuning tiers.
- A TM attraction can have an empty name; Deezer answers an empty `q` with an error (HTTP 200,
  `{"error": {"code": 500, "type": "ParameterException"}}`), which originally aborted enrichment. Deezer reports
  *all* errors with HTTP 200; quota is `code: 4`.
- `fmt_price` used `:,.0f`, so $59.50 displayed as "$60". Prices must be exact; now shows cents when non-integer.
- Only ~15% of TM events have `priceRanges`; presale windows on ~37%.

## Process mistakes

- Spent a turn waiting on the user's `.env` edits that were unsaved in their editor. Check the file on disk
  (`sed -E 's/=.+/=<set>/' .env`, which never prints values) and say so early.
- The first live run created 53 alerts from detectors written against invented data. Do a `--no-drafts` run
  and inspect alerts before letting the LLM spend 10 minutes on them.
- Rebuilding the DB loses review state (draft statuses, dismissals). Only OK while nothing has been reviewed.
  Once the user is using the dashboard, migrate instead of rebuilding.

## LLM output (from reviewing the first 8 real drafts, 2026-10-21)

- **Anything in `facts` can be copied verbatim into the caption.** An instruction placed in facts
  (`"note": "Presale codes are not in our data..."`) came out word for word in a caption. Instructions go in
  the prompt/format guide, never in facts.
- **Right date, wrong weekday** ("Mon, Nov 21" for a Saturday) passed the original checker, which only
  compared month+day. `check_facts` now verifies weekday+date combos as a unit.
- Placeholders come back with different casing (`[Artist]` vs fact key `artist`); `fill_placeholders`
  normalizes case and spaces.
- Hashtag typos on artist names (`#calvinnharris`, `#elderbroom`) and punctuation (`#Rockin'Philadelphi`):
  `clean_hashtags` strips punctuation and snaps near-misses (difflib ≥ 0.85) to the exact artist name.
- The model said tickets "are live now" before the sale opened, and told people to find codes for a general
  on-sale. Neither is caught automatically; the presale guide now addresses both explicitly. **Read presale
  captions before posting.**
- Alert facts gained keys over time (`history` arrived with setlist.fm). Comparing old vs new facts naively
  flagged 6 presale alerts as "changed" when only `history: None` had appeared. Treat None and missing alike.
- It guessed artist pronouns; the system prompt now says to use the name or "they".

## Feeds and dates

- **Tonight's shows drop out of Ticketmaster once they start** (it only returns events that haven't started).
  The store used `local_date >= today` for "removed", so every evening run marked tonight's shows removed.
  Fixed to `local_date > today` (2026-10-21). Any new source with the same behavior is covered by this.
- Venue JSON-LD start times are UTC (`2027-01-08T00:30Z` = 7:30 PM Jan 7 in Philly). Always convert to
  `America/New_York` before taking the date.
- Splitting "A w/ B & C" into acts: split support on commas only; "&"/"and" are inside band names.
- A heredoc wrote `"\\0"` into store.py as a literal NUL byte ("source code string cannot contain null bytes").
  Same family as the `\b` trap above: use the Edit tool.

## Cloudflare

- `wrangler pages project create` exited 0 but **did not create the project** (non-interactive/CI mode printed
  a config suggestion instead). Creating it with `POST /accounts/{id}/pages/projects` worked. Trust the API's
  `success` field, not wrangler's exit code; verify with `GET .../pages/projects/<name>`.
- **The GitHub repo is public.** wrangler writes `.wrangler/cache/pages.json` (account ID) into the working
  dir; it got committed once (`6927b43`) before `.wrangler/` was gitignored. Check `git status` for stray
  files before every commit, and keep account/infra details out of docs.
- Same class of slip, 2026-10-21: niche tests used *real* artist names from the user's listening history (with
  fake counts) and got pushed on a feature branch. Fixed by invented names + squash-merge + branch deletion.
  Use made-up artist names in any test touching listening data.
- If `<name>.pages.dev` is taken, Cloudflare silently appends a suffix (e.g. `<name>-2h7.pages.dev`).
  `publish.site_url()` reads the real `subdomain` from the API; never hardcode `<name>.pages.dev`.
- A new project's hostname doesn't resolve until the first deployment, so the Access guard can't pass before
  then. The fix was a data-free placeholder deploy first, then the check, then real data. Don't "fix" it by
  disabling the guard. Per-deployment hostnames take ~1–2 min longer to resolve.
- Every deploy is also served at `<hash>.<project>.pages.dev`. The Access app must include `*.<host>`;
  the guard checks a made-up `guardcheck.<host>` to prove the wildcard is locked.
- `python -I` ignores `PYTHONIOENCODING` (`-I` implies `-E`), so wrangler's box-drawing/emoji output crashed
  `print` on cp1252. In `-I` scripts call `sys.stdout.reconfigure(encoding="utf-8")`.

## Tests

- `tests/factory.tm_event()` builds TM-shaped events; most bugs above came from the factory being cleaner than
  real data. When you find a real-data quirk, add it to a test.
- `responses` mocks HTTP; nothing in the suite touches the network. `NOW` in tests is Wed 2026-10-21 16:00 UTC.

## Web lookups (Perplexity / Gemini)

- Put today's date in the prompt. Without it Gemini searched for "news 2024".
- Perplexity `/v1/agent` with `preset: fast`: `output_text` is null. The answer is in `output[type=message]`, and
  its `[n]` citations point at `output[type=search_results].results[].id`, which are not 1..k (e.g. [2][5][8]).
- `gemini-2.5-flash` returns 404 for new keys; Gemini 3+ has no search grounding on the free tier (429).
- Answers use markdown: bold dates with *italic* titles nested inside. The card renders those without raw HTML.
- The dev worktree has no `.env`: pass `PERPLEXITY_API_KEY` (and `LISTENING_DB`) as env vars to the test server,
  read from the main `.env` without printing them.

## Genres (2026-10-22)

- Ticketmaster's own listing genre is sometimes wrong (Alter Bridge listed as Dance/Electronic, 2hollis as Rock).
  Since 2026-10-22 Deezer's release genre replaces it across genre families (decisions.md "Deezer genres").
  Deezer's "Alternative" covers indie pop too, so it only fills, never replaces (DEEZER_FILL_ONLY).
- Alert facts pick up a corrected genre on the next run's refresh; a redraft before that still sees the old one.
- Tests that re-ingest with `store.upsert_events` must send the whole listing again: shows left out are marked
  removed (that's the real behavior), which silently drops them from `upcoming_events`.

## Slides / canvas checks

- Headless Edge can't click, so to screenshot the slides overlay, bundle a scratch entry that mounts it with
  `dashboard/node_modules/.bin/esbuild ... --define:import.meta.env={"VITE_STATIC":"0"}` (without it `api.ts`
  throws and the page is blank) and drop it in `dashboard/dist/` (gitignored; the API serves any file there).
- Headless Edge with a reused `--user-data-dir` caches JS: add `?v=<timestamp>` to script URLs between rebuilds.
- Canvas text fitting: check every word fits the line *before* wrapping at a size, or the largest size wins by
  splitting "weekend" into "weeken / d".

## Integration (2026-10-22)

- `tracker.run --from-cache` still calls Deezer (free) for enrichment. For a no-network re-detect on a DB
  copy, call `detectors.run_all` + `save_alerts` directly (see the QA scripts pattern in worklog).
- The API refuses any Host that isn't 127.0.0.1/localhost/::1. A test server reached by another name (or a
  TestClient outside pytest) gets 403 "unknown host".

## Phone editing (2026-10-22)
- The tunnel's public hostname must keep the default Host header (no "HTTP Host Header" override), or every
  request is "unknown host". Create the Access app (`python -m tracker.remote --setup`) **before** adding the
  hostname; without it Cloudflare passes requests unauthenticated (we still 403 them, but it's noisy).
- `tracker.remote --setup` takes the team domain from `.env` (CF_ACCESS_TEAM_DOMAIN); the Pages-redirect fallback is gone with the Pages site. Run it from the main
  folder (it needs PAGES_PROJECT from `.env`).
- Tests blank REMOTE_* in conftest; `tests/test_remote.py` signs its own tokens with throwaway RSA keys.
- Claude drafts: tests must never call the API. conftest blanks ANTHROPIC_API_KEY and sets DRAFT_PROVIDER=ollama;
  tests that want Claude fake `anthropic.Anthropic`. Fakes of `llm.generate` must accept `conn=`.
- Trying prompts or models on real alerts: copy the DB first (sqlite backup to the scratchpad, TRACKER_DB=copy) so
  test drafts and llm_calls rows don't land in the live queue.


- Job polling (App.tsx): don't detect "job finished" by having seen `running: true` in a poll. Templated drafts
  finish in under 3 s, between polls. Compare the finished job (`started|finished`) instead.

## Music sources (M8, 2026-10-24)

- **youtube.com's robots.txt disallows `/feeds/videos.xml`.** The channel RSS feeds look like the free route but
  aren't allowed; YouTube goes through the Data API (YOUTUBE_API_KEY). Never log the request URL: it has the key.
- **MusicBrainz 503s are often load-shedding, not our rate** (`Retry-After: 0`, X-RateLimit-Remaining high, ~1 in 3
  requests on 2026-10-24). Treating 503 as "stop for this run" left 331 of 335 artists without an mbid for days.
- **Any new keyed API must be blanked in tests/conftest.py.** LAST_FM_API_KEY wasn't (M7), so every test that ran
  `news.run` made live Last.fm/Deezer/ListenBrainz calls from the main folder (where `.env` exists) but not from the
  dev worktree (no `.env`), which hid it. A suite that suddenly takes minutes is a sign.

## Philly experiences (P2-14, 2026-10-24)

- **A new Ticketmaster feed's first read is a baseline.** `run.new_feed_ids` marks events from a team or comedy
  that the DB has never stored, so they don't log "new" changes (31 "just announced" comedians otherwise). Adding a
  team to `SPORTS_TEAMS` later works the same way.
- **"Free" only from an experience listing or from you.** `fmt_price(..., free_ok=is_experience(e))`: Ticketmaster
  has $0-$0 listings that are gaps, which now state no price. `check_facts` flags "free"/"cheap"/"under $X" unless
  the facts say so ("feel free" and names like "Free Nationals" pass).
- **Experience events can start in the past.** `EVENT_VIEW_SQL` keeps an event while `COALESCE(end_date,
  local_date) >= today`, and the store's "removed" check uses the same date. Code that assumes `local_date >= today`
  for every upcoming event is wrong for a running market.
- **Recurring listings:** Center City District lists every date separately. `collapse_tribe` merges back-to-back
  days (gap <= 2) into a range and shows a weekly/monthly series as its next date only (a range would claim every
  day in between).
- **Hand-added events sync on save** through `store.upsert_events(..., protected_prefixes=("",))`: a one-event batch
  must not mark everything else removed. Their notes never reach facts.
- `tests/conftest.py` blanks `EXPERIENCE_SOURCES`, or the end-to-end tests would read the real sites.

## Drafts with notes (redraft.py, 2026-10-26)
- A note is an instruction, not a fact: shows enter the facts only through `add` (event ids -> `event_facts`).
  `check_facts` still runs against the edited facts.
- `drafts.facts` stays the ALERT's facts at writing time; `drafts.instructions` holds the edits (plus `added`/
  `left_out` labels). `draft_row` filters the user's own edits out of `facts_changes` by those labels, which
  must match `detectors._show_label` ("artist (date)").
- Slides use `redraft.edited_facts` (the newest draft's edits), whatever that draft's status.
- Tests stub `llm.generate` with lambdas that don't take `note`: drafts.py passes it only when set (`_note`).
- Ticket URLs aren't always unique per listing; `_find` checks name and date after matching the URL.

## Vinyl (vinyl.py, vinyl_research.py, 2026-10-28)
- Kind `vinyl` for every vinyl idea; the format lives in `facts.format` (`vinyl_lore`, `vinyl_value`...). Vinyl
  ideas are left out of `/api/alerts` unless `kind=vinyl` (the Vinyl tab), and `draft_pending` never drafts
  them: a researched draft is Sonnet + a Haiku claim check (a few cents), so it only runs on a click.
- Ideas are raised once per period (`save_ideas` skips existing keys) and never refreshed: prices move weekly
  and refreshing would churn drafts. Weekly/daily story picks are skipped when this period's key exists, or
  they'd use up records from the day's other picks (`recently_featured` counts every idea raised in 90 days).
- Discogs: one row per copy (`instance_id`); two pressings of an album are two rows sharing a master. Artist
  names lose Discogs' "(2)" suffix. `album_title` strips edition tags for MusicBrainz and Wikipedia.
- "Colored vinyl" needs a color word in the format's free text (`is_colored`): that text also holds things like
  "10th Anniversary" or "Cover Smear" (a variant name).
- Discogs' suggested prices are by condition from recent sales; we show Near Mint and say "Discogs suggests".
- Sources: a Wikipedia page is only used when its title matches the album (ignoring "(… album)") and its text
  names the artist and calls it an album/mixtape/EP near the top. A lookup that found nothing leaves a
  `checked:album`/`checked:artist` marker row in `vinyl_sources` so it isn't retried every draft (60 days).
- Dig deeper uses the basic `web_search_20250305` tool: with `_20260209` (dynamic filtering) the text came back
  without citations. It keeps only the passages Claude's search cited (`cited_text`), per URL, as `web` sources;
  the claim check reads those, never Claude's own summary. Searches are logged at $0.01 each in `llm_calls`.
- Research and writing are separate calls: the writer returns structured JSON (claims with source numbers), while
  the web-search pass is read for its citations, which come back on plain text blocks.
- No real artist names in vinyl tests or comments: the fixtures use the invented names.
