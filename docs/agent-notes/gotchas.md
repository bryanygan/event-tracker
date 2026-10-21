# Gotchas and mistakes already made

## Writing code from the shell

- **Bash heredocs + Python string escapes corrupt regexes.** Writing `"\\b"` inside a quoted heredoc fed to
  Python, which then wrote a normal string, produced literal backspace (`\x08`), `\x01`, `\x02` bytes in
  `tracker/llm.py` and later `tracker/config.py`. The code still imported; the regexes just silently didn't match.
  `sed -n` output hides these bytes (`cat -A` shows `^H`). **Use the Edit/Write tools for anything containing
  regex backslashes.** Check with `grep -lP '[\x01-\x08]' tracker/*.py tests/*.py`.
- `sed -i` with `\n` and backslashes is just as fragile; prefer Edit.

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
- `ollama list` with the server down can hang and/or launch the Ollama tray app (`ollama app.exe`). The user
  keeps Ollama off; if you start it, stop both `ollama` and `ollama app` afterwards.
- Ollama on this version (0.32) didn't leave an `ollama_llama_server.exe`; the `taskkill` in `ollama_session` is
  a harmless no-op then. Verified 2026-10-21: after a cold-start draft run, no ollama processes remained.

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
- It guessed artist pronouns; the system prompt now says to use the name or "they".

## Tests

- `tests/factory.tm_event()` builds TM-shaped events; most bugs above came from the factory being cleaner than
  real data. When you find a real-data quirk, add it to a test.
- `responses` mocks HTTP; nothing in the suite touches the network. `NOW` in tests is Wed 2026-10-21 16:00 UTC.
