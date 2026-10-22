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

**Sixers from the Ticketmaster basketball feed, filtered to `SPORTS_TEAMS`** (2026-10-21). The feed also has
Villanova, Delaware Blue Coats, NCAA women's championship, Big 5 Classic; the user asked for the Sixers, so only
home games of listed teams are kept (TM lists the home team first). Music and sports raw events go into ONE
`upsert_events` call, because the store marks anything missing from the batch as "removed". No prices or
presales exist on regular-season games. Games score `SPORTS_BASE_SCORE` + marquee opponent + weekend (Deezer
fans are meaningless for a team; Deezer and setlist.fm skip `segment='Sports'`).

**Philly history from setlist.fm** (2026-10-21). Per artist: `search/setlists?artistMbid=…&cityName=Philadelphia`
(whole Philly history in one call; the fuzzy city match includes Camden/Chester) plus page 1 of the artist's
setlists (recent suburban venues), all filtered by venue coords within `RADIUS_MILES`; newest past hit wins.
The first version paged the last 100 setlists, which reached only ~18 months back for heavy tourers (found 20/40;
the new way found 90/100). Only a *found* show is used: crowd-sourced data, so "none found" never becomes
"first time ever". `COMEBACK_MIN_YEARS = 3` (2-year gaps made weak hooks: "since 2024"). Comebacks get
`COMEBACK_BONUS`, a "Back after N years:" title, a script line, an on-screen line, a caption line crediting
setlist.fm (their API terms ask for credit), and a "Verify" warning on the draft. Up to 100 artists/run,
cached 30 days, ~3 calls/artist (limit 1,440/day).

**Multi-act last shows are labeled** (2026-10-21, after the user confirmed Calvin Harris's "last Philly show" was
Q102 Jingle Ball 2018, a 15-act radio show). setlist.fm has no festival flag, so we count setlists at that
venue+date (`search/setlists?venueId&date`, 1 call): Jingle Ball 9, Made in America 16, a normal concert ~3.
`MULTI_ACT_MIN = 5`. Such comebacks still count (they haven't played Philly since), but scripts add "Last time
was a 9-act lineup at <venue>" and captions say "(a 9-act lineup, per setlist.fm)", so viewers don't assume it
was their own concert.

**Venue calendars, not ticket-seller integrations** (2026-10-21). AXS/DICE/Tixr have no public APIs and
scraping their sites likely breaks their terms; venues' own pages carry schema.org Event JSON-LD (generated by
Tixr/DICE), robots.txt allows us, and it's one request per venue per run. Rules: Ticketmaster's copy wins on
duplicates (same date + venue + overlapping name words); a venue's first read is a baseline (no "new");
a failed venue's shows are protected from "removed" and it shows up in the dashboard's source warning
(`source_runs` table → `/api/summary.source_problems`); `--from-cache` never fetches venue sites. Shows get no
genre and no presales, so they mainly feed weekend roundups, new-show alerts (only if notable) and history.
The user said small-venue shows rarely get TikTok traction but are worth having for niche/notable artists,
so they score through the normal formula (small venue tier + Deezer) and rarely alert.

**Niche boost from the user's own listening** (2026-10-21). The user covers rap, hyperpop, pop, R&B, EDM,
bedroom/indie pop (open to anything notable) and asked to use their ttspotslideshow history. `tracker/listening.py`
reads its `plays.db` read-only (`LISTENING_DB` in .env): plays in the last 2 years → +20/+12/+6 at 200/50/10
plays, +5 for the genre lane (TM genre or the slideshow's `artist_genres.primary_bucket`, which also gives
venue-calendar shows a genre). 25+ plays = "in your rotation": new-show and presale alerts bypass the score bars
and get a 🎧 title prefix. **Privacy (public repo):** no listening data in code, notes or tests (fake DB in tests);
`your_plays` lives in alert facts for the dashboard but `_prompt_facts` strips it, so drafts never mention it.

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

**Presale and status-change drafts are templated** (2026-10-21, user chose this over a 7B model or hand-editing).
`tracker/templates.py` writes the voiceover script *and* the caption from facts; the LLM writes only the hook and
hashtags. Reason: in real drafts the 3B model paired the wrong time with the wrong sale ("show opens Friday" for a
Thursday presale; "Eagles Presale" read as an Eagles show), which `check_facts` can't catch because every token is
in the facts. Captions were templated too (a step beyond the original "LLM does hook + caption" plan) because the
model's captions either pasted the whole script or had the same pairing errors. A flagged hook falls back to
`templates.fallback_hook`. Scripts use spoken dates ("Thursday, October 22 at 10 AM Eastern") via `speakable()`;
`check_facts` still matches them. Wording varies per artist (hash-picked opener/closer/CTA). Weekend,
stacked-month and new-tour drafts are still fully LLM-written.

**Max 8 drafts per run**, highest-score alerts first (~10 min of CPU).

**Ollama is started on demand and stopped after** (`llm.ollama_session`). The user had disabled Ollama's
auto-start (`Startup\Ollama.lnk.disabled`), so we don't keep ~2.5 GB in RAM next to Homebridge. If Ollama is
already running, it's left alone.

## Operations

**Cloudflare Pages = read-only snapshot behind Cloudflare Access** (2026-10-21, user's choice over an
interactive tunnel or a public site). The same React app is built with `VITE_STATIC=1`
(`npm run build:static` → `dashboard/dist-static`) and reads `data/*.json` instead of the API; edit controls are
compiled out. `tracker/publish.py` exports the JSON (by calling the API's own endpoint functions, so it's the
same data) into `data/site/`, then runs `wrangler pages deploy` (pinned wrangler 4.148.0, a devDependency of
`dashboard/`). Runs at the end of every pipeline run and from a "Publish snapshot" button. **Guardrail:** before
uploading it GETs `https://<project>.pages.dev/` and refuses unless the answer is a redirect to
`*.cloudflareaccess.com` (`PAGES_ALLOW_PUBLIC=1` overrides). `_headers` adds noindex/no-referrer/DENY framing;
`robots.txt` disallows all. `tests/conftest.py` blanks all Cloudflare config so tests can never deploy.

**Twice-daily schedule (6:30 AM, 4:30 PM).** Presales/on-sales mostly open 10 AM local Tue–Fri; with a 72h
window that gives ≥1 day notice. Tasks run windowless (`pythonw`), Priority 7 (below normal), only while the user
is logged on (Interactive), `MultipleInstances IgnoreNew`, `StartWhenAvailable`.

**One background job at a time in the API** (`_job_lock`) so two LLM generations never run at once on the N95.

**Per-request DB connections, always closed** (`api.get_db` dependency). Open SQLite handles lock the file on
Windows, which blocked rebuilding the DB.

## Creator features (2026-10-21, from creator-roadmap.md)

**Alerts are the "content opportunities".** The roadmap proposed a separate `content_opportunities` table. An
alert already is one video idea (stable key, format, grouped events, facts), so a parallel table would only add
syncing. Added instead: `alerts.event_ids/post_by/expires_at` (set by detectors from data), user state
(`shortlisted`, `snoozed_until`, `note`), and `tracker/opportunities.py`, which computes the score, reasons,
caveats and workflow stage on every request (nothing derived is stored, so it can't go stale). Stage is derived
from alert/draft/post state; existing alert and draft statuses are unchanged.

**Opportunity score is separate from notability and labelled experimental.** Weights 25/25/20/15/10/5 from the
roadmap (`config.OPPORTUNITY_WEIGHTS`, versioned by `OPPORTUNITY_RULES_VERSION`). Unknown components are left out
and their weight redistributed; `coverage` shows how much was known. Search demand has no source, so it's always
unknown (shown once in the queue header, not on each card). Bands 80/65 were set from the first real queue: at
70/50, 14 of 21 ideas were "strong" because presale alerts are always <72h out and max the timing component.
Each logged post saves the evaluation at posting time (`posts.opportunity_snapshot`) for later calibration.

**Alert facts refresh; drafts don't.** `save_alerts` used INSERT OR IGNORE, so a Wednesday weekend roundup never
saw later changes. Now re-detected alerts update facts/title/score (old version → `alert_history`), except
dismissed alerts and alerts with a logged post. Each draft stores the facts it was written from; the dashboard
shows `facts_diff` against the current facts. Re-drafting is the user's call (LLM time). `hours_until_open` and
`your_plays` are volatile and ignored; None and missing keys count as equal.

**Posts and metrics are typed in by hand.** No TikTok API or scraping. Comparisons use the snapshot nearest
24h (18–36h) / 7d (5–10d), medians, per-1k-view rates, and mark groups under `ANALYTICS_MIN_POSTS` (3) as too
few. Unrecorded metrics are NULL, never 0.

**Angles are deterministic (P0-06).** `tracker/angles.py` builds 2–4 framings per idea (news / fan / utility, plus
comeback when setlist.fm supports it) with hook, brief, video style and CTA, from facts only. All hooks pass
`check_facts` (tested against every real alert). Picking an angle on a templated format makes a complete draft
with no model call; on LLM formats the hook is fixed and the angle goes into the prompt. Hooks avoid claims the
facts can't time-check: "How to get X tickets", not "X tickets go on sale Friday" (facts have no year, so a past
on-sale can't be detected). Opinion angles ("I'd pick this over…") are left to the creator.

## Web context (2026-10-22, user approved; `tracker/context.py`)

- **Perplexity, not Gemini.** Same dated prompt for 5 queue artists, checked against TM data and the web:
  Perplexity had no material errors and gives full URLs with publish dates; Gemini 3.8 Flash invented a presale
  date and called an on-sale show sold out, and its links are opaque redirects. Google's grounding terms also
  require showing results verbatim with their Search Suggestions, to the asker only. Gemini key kept, unused.
- **Reading material, never facts.** Answers are shown on the card with sources and a "check before you say it"
  label. They don't feed scores, angles, drafts or the fact checker. Only public facts go out (artist, show date,
  venue); never listening data.
- **Cost caps:** one lookup per artist reused for 7 days; 10 lookups per ET day including failures; the pipeline
  uses at most 5 of those, on "strong" single-artist ideas only; replays (`--from-cache`) and tests never call it.
  Measured ~$0.0016 per lookup → worst case ~$0.50/month.
- Single-artist formats only (presale, new_tour, status_change). Roundups and games have no single subject.
