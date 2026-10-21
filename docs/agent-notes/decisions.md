# Decisions and why

Each entry: the decision, the reason, and what was rejected. Dated so you can tell what's stale.

## Architecture

**The LLM writes, it never fetches or decides facts.** (brief requirement)
Fetchers → SQLite → rule-based detectors → LLM only turns an alert's `facts` JSON into hook/script/caption/
hashtags. Detectors pre-format every fact ("Sat, Oct 24", "8:00 PM", "Thu, Oct 22 at 10:00 AM ET") so the model
never does date math.

**On-screen text is built in Python, not by the model** (`llm.onscreen_lines`). Dates/venues on screen are
what viewers screenshot, so they must be exact.

**`check_facts` is a tripwire, not a guarantee.** Flags dates/times/prices/numbers not in facts, "code XXXX"
patterns, and `[Placeholder]` leaks. Shown as ⚠ in the dashboard. Bare numbers are compared against the
facts plus a derived `show_count` (the model naturally says "3 shows").

**Python + SQLite + FastAPI + React/TS (Vite).** Matches the user's stack. FastAPI also serves the built React
app: one process, one port. SQLite in WAL mode so the dashboard reads while a run writes.

**Ports:** dashboard 127.0.0.1:8765 (localhost only). Homebridge UI is 8581. The user's slideshow project
serves 0.0.0.0:8000. Vite dev server 5173 proxies `/api` to 8765.

## Data

**Ticketmaster radius search, not a venue list.** City Hall (39.9526,-75.1652), 30 mi, `classificationName=music`,
240 days ahead. Real volume (2026-10-21): ~500 events = 3 API calls. Window-splitting exists for the
1000-result deep-paging cap but hasn't been needed yet for music alone.

**Only the TM consumer key is used** (sent as `apikey`). User stores consumer key + secret
(`TICKETMASTER_CONSUMER_KEY/SECRET`); config also accepts legacy `TICKETMASTER_API_KEY`.

**First successful run is a baseline.** Stores everything, logs no "new" changes. Otherwise day one would flag
~500 "just announced" shows. Consequence: `new_tour` alerts only appear from the second real fetch onward.

**Popularity from Deezer, not Spotify** (2026-10-21). Spotify Web API for this (development-mode) app returns
artist objects with only `external_urls, href, id, images, name, type, uri`: no popularity, followers, or genres;
`/artists/{id}/top-tracks` returns 403. Deezer `GET https://api.deezer.com/search/artist?q=` returns `nb_fan`
with no key. Mapped `popularity = clamp(20*(log10(fans)-2), 0, 100)` (100 fans→0, 10k→40, 1M→80, 10M→100).
Deezer undercounts some US acts (Zach Bryan 125K vs Olivia Rodrigo 1.8M), so it's 50% of the score, not 100%.
Exact case-insensitive name match, highest `nb_fan` wins. Rejected: Last.fm (needs another key; still a good
future second signal). `spotify.py` was deleted; it's in git history (`09e41b9`) if ever needed.

**Genres come from Ticketmaster classifications.** "Other"/"Undefined" are stored as NULL.

**Venue tiers are hand-maintained** in `config.VENUE_TIERS`, substring match, first match wins (specific names
before generic, e.g. "td pavilion" before "mann"). Real TM names differ from common names:
"The Met Presented by Highmark", "Skyline Stage at Highmark Mann", "Xfinity Mobile Arena" (formerly Wells Fargo
Center; both keys kept). Unknown venues get `DEFAULT_VENUE_TIER = 20`.

**Event names are cleaned** (`ticketmaster.clean_event_name`): strips "(21+ Event)", "(21 and Over)", "(18+)",
"- All Ages (under 16 with adult)", NBSPs. Deliberately keeps things like "(30th Anniversary)".

## Detectors

**Presale alerts: one per artist** (2026-10-21). Real shows have 4–5 windows (Citi, Live Nation, venue,
"Ticketmaster Presale", VIP, general). First version made one alert per window → 47 alerts. Now: triggered if any
window opens within 72h; facts list all future windows (`all_sales`). VIP/package/hospitality/suite/parking
windows are ignored. `PRESALE_MIN_SCORE = 45`. Dedupe key `sale:<artist_id>:<first show date>`.

**Weekend roundup is created from Wednesday on**, max 2 picks per genre, needs ≥3 shows. Key `weekend:<friday>`.

**Stacked month ignores the current month after the 15th** ("October is stacked" on Oct 21 is useless).
Counts one entry per artist; notable = score ≥ 50.

**Alerts are deduped by `key` (UNIQUE)**: re-runs never re-raise the same alert.

## LLM

**qwen2.5:3b** (2026-10-21). On the Intel N95 (CPU only): ~6.5 tok/s, ~40–70 s per draft. Compared head to head
with llama3.2:3b on identical facts: llama copied the prompt examples nearly verbatim (including the subjective
"or X if you want something smaller") and invented a `[link]`; qwen adapted the examples. llama3.2:1b
(pre-installed) wasn't seriously considered: too weak for hooks.

**Prompt = rules + one example per format with [placeholders].** Examples improved hooks a lot over rules
alone. Placeholders are deliberate: if the model copies one, it's visible. `fill_placeholders` replaces
`[key]` when `key` is a real top-level fact (e.g. `[sale_opens]`); others (`[Artist]`) are left and flagged.

**Post-processing over prompting for formatting.** The model ignored "no hashtags/emoji in spoken text", so
`spoken()` strips them from hook/script. Hashtags are normalized and `#philly` is forced in.

**Ollama structured output** (`format` = JSON schema) for reliable parsing. `keep_alive: 2m`.

**Max 8 drafts per run**, highest-score alerts first (~10 min of CPU).

**Ollama is started on demand and stopped after** (`llm.ollama_session`). The user had disabled Ollama's
auto-start (`Startup\Ollama.lnk.disabled`), so we don't keep ~2.5 GB in RAM next to Homebridge. If Ollama is
already running, it's left alone.

## Operations

**Twice-daily schedule (6:30 AM, 4:30 PM).** Presales/on-sales mostly open 10 AM local Tue–Fri; with a 72h
window that gives ≥1 day notice. Tasks run windowless (`pythonw`), Priority 7 (below normal), only while the user
is logged on (Interactive), `MultipleInstances IgnoreNew`, `StartWhenAvailable`.

**One background job at a time in the API** (`_job_lock`) so two LLM generations never run at once on the N95.

**Per-request DB connections, always closed** (`api.get_db` dependency). Open SQLite handles lock the file on
Windows, which blocked rebuilding the DB.
