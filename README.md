# Philly Events → TikTok drafts

Tracks Philly-area concerts, notices things worth a video, and drafts scripts for you to review.
Nothing posts automatically. Brief: [event-tracker-pipeline.md](event-tracker-pipeline.md).
Working on this with an AI agent? Start with [docs/agent-notes/](docs/agent-notes/README.md).

```
Task Scheduler (6:30 AM, 4:30 PM)
  └─ python -m tracker.run
       1. fetchers/ticketmaster.py   30 mi around City Hall, next 240 days, music only
       2. store.py                   upsert into SQLite, log changes (new / price / status / date / presale)
       1b. same fetcher, basketball   Sixers home games (teams listed in config.SPORTS_TEAMS)
       3. fetchers/deezer.py         headliner popularity from Deezer fan counts (no key; cached 14 days)
       3b. fetchers/setlistfm.py     last Philly-area show per artist → "first Philly show since 2019"
       4. detectors.py               new tour · presale/on-sale in 72h (one alert per artist) · weekend ·
                                     stacked month · cancel/postpone · Sixers home games this week · big game
       5. drafts.py + templates.py   presale/status/Sixers: Python writes script + caption, Ollama the hook;
          + llm.py                   other formats: Ollama writes everything. Top 8 new alerts per run.
       6. publish.py                 read-only snapshot → Cloudflare Pages (behind Access)
Dashboard (always on): python -m tracker.api → http://127.0.0.1:8765
```

## Setup

```powershell
cd C:\Users\Admin\Documents\events-tracker
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # then fill in the keys
ollama pull qwen2.5:3b
cd dashboard; npm install; npm run build; cd ..
```

## Daily use

| Do this | Command |
|---|---|
| Open the dashboard | http://127.0.0.1:8765 |
| Run the pipeline now | "Run pipeline now" button, or `.venv\Scripts\python -m tracker.run` |
| Data + alerts only, no LLM | `.venv\Scripts\python -m tracker.run --no-drafts` |
| Re-run on the last pull (0 API calls) | `.venv\Scripts\python -m tracker.run --from-cache` |
| Run tests | `.venv\Scripts\python -m pytest` |
| Install / remove scheduled tasks | `powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1` / `uninstall_tasks.ps1` |
| Dashboard dev mode (hot reload) | `cd dashboard; npm run dev` → http://127.0.0.1:5173 |

Review flow: open **Alerts & drafts**, expand **Facts** if something looks off, edit the draft in place,
copy the hook/script/on-screen text/caption, and set the status (approved → filmed). **New draft** asks the
model again (~1 minute).

## How facts stay correct

- The LLM only sees the alert's `facts` JSON. Dates/times/prices are pre-formatted ("Sat, Oct 24", "8:00 PM").
- **On-screen text is built in Python from facts**, not by the model.
- `llm.check_facts` flags any date, time, price, or number in the model's text that isn't in the facts, plus
  anything that looks like a presale code or a `[placeholder]`. Flags show as a yellow ⚠ on the draft.
  It's a tripwire, not a proof: still read the script before filming.

## Tuning

Everything adjustable is in `tracker/config.py`: radius, lookahead, presale window, stacked-month thresholds,
venue size tiers, junk filters (parking passes etc.). Notability weights are in `tracker/scoring.py`.
The prompt and per-format examples are at the top of `tracker/llm.py`.

## Decisions and why

- **Python + SQLite + FastAPI, React/TS dashboard.** Fits your stack. The FastAPI server also serves the built
  React app, so there's one process and one port.
- **Port 8765, bound to 127.0.0.1.** Away from Homebridge (8581), and not reachable from the network.
- **Ollama starts only when needed.** Its auto-start is disabled on this PC, so the pipeline starts `ollama serve`
  for drafting and stops it afterwards, freeing ~2.5 GB of RAM for everything else. If Ollama is already
  running, it's left alone.
- **qwen2.5:3b over llama3.2:3b/1b.** About 6.5 tokens/s on the N95, ~1 minute per draft. In side-by-side tests llama3.2:3b
  copied the prompt examples word for word and invented a `[link]`; qwen adapted them and stuck to the facts.
- **First run is a baseline.** Everything already on sale is stored but not treated as "new", or day one
  would flag 1000 "just announced" shows.
- **Ticketmaster deep paging cap.** The API stops at 1000 results per query, so the fetcher splits the
  date range in half until each window fits.
- **Deezer instead of Spotify for popularity.** Spotify's API no longer returns popularity, followers or genres
  for development-mode apps (checked Oct 2026: artist objects come back with only id/name/images, and top-tracks
  is 403). Deezer's public search gives fan counts with no key. It undercounts some US acts (Zach Bryan: 125K),
  so it's blended with venue size and tour size instead of used alone. Genres come from Ticketmaster.
  The Spotify keys in `.env` are currently unused.
- **One presale alert per artist.** Real data had 4–5 sale windows per show (Citi, Live Nation, venue, VIP,
  general); one alert lists them all. VIP/package on-sales are ignored (`IGNORED_SALE_PATTERNS`).
- **Event names are cleaned** of "(21+ Event)", "- All Ages (under 16 with adult)" and non-breaking spaces.
- **Twice-daily fetch.** Presales and on-sales mostly open at 10 AM local; 6:30 AM and 4:30 PM runs with a 72h
  window give at least a day's notice. A full pull of ~500 Philly music events is 3 API calls against the 5000/day limit.
- **Below-normal task priority**, so Homebridge always wins the CPU.

## Not done yet (next phases)

- Food, festivals, other things to do (no clean API; needs scrapers).
- Other teams/leagues: add Ticketmaster attraction names to `SPORTS_TEAMS` (and a classification to
  `SPORTS_CLASSIFICATIONS`), e.g. Villanova or the NCAA Women's Championship from the basketball feed.
- "Under $40 this month" format (only ~15% of Ticketmaster events include a price range).
- Phone notifications for big presales; slideshow/asset generation.
