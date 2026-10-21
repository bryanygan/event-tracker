# Environment (checked 2026-10-21)

## Machine

- Windows 11 Pro mini PC, **Intel N95, ~12 GB RAM, no discrete GPU** (Intel UHD). LLM runs on CPU.
- Python 3.13.7, Node 24.15, npm 11. Project venv: `.venv` (`.venv\Scripts\python`, `pythonw` for windowless).
- Shells available to agents: PowerShell 5.1 and Git Bash. Bash paths look like `/c/Users/Admin/...`.
- Project: `C:\Users\Admin\Documents\events-tracker`.

## Other things running on this PC. Don't disrupt them.

- **Homebridge** (hb-service, Node.js), UI on port **8581**. Also a `homebridge-kasa-python` helper
  (`C:\Users\Admin\.homebridge\kasa-python\...startKasaApi.py`).
- **The user's Spotify "Now Playing" slideshow project**: `C:\Users\Admin\Documents\ttspotslideshow`,
  `dashboard_server.py` listening on **0.0.0.0:8000**. Possibly reusable for slideshow/asset generation later.
- **Ollama 0.32** at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`, API 127.0.0.1:11434. Auto-start is disabled
  by the user. Models: `llama3.2:1b` (pre-existing), `qwen2.5:3b` (default), `llama3.2:3b` (pulled for comparison;
  could be removed to save ~2 GB).

## Our scheduled tasks

| Task | Trigger | Action |
|---|---|---|
| `PhillyEvents-Pipeline` | daily 6:30 AM, 4:30 PM | `.venv\Scripts\pythonw.exe -m tracker.run` |
| `PhillyEvents-Dashboard` | at logon (restarts 3× on failure) | `.venv\Scripts\pythonw.exe -m tracker.api` |

Both have working dir = project root, Priority 7, Interactive logon. Remove with `scripts\uninstall_tasks.ps1`.

## Files that aren't in git

- `.env`: `TICKETMASTER_CONSUMER_KEY`, `TICKETMASTER_CONSUMER_SECRET`, `SPOTIFY_CLIENT_ID`,
  `SPOTIFY_CLIENT_SECRET`, optional `OLLAMA_URL`, `OLLAMA_MODEL`, `DASHBOARD_PORT`, `TRACKER_DB`.
  **Never print values.** To check what's set: `sed -E 's/=.+/=<set>/' .env`.
- `data/tracker.db` (+ `-wal`, `-shm`), `data/cache/tm_last_pull.json`, `logs/pipeline.log`, `logs/console.log`.
- `dashboard/node_modules`, `dashboard/dist` (rebuild with `cd dashboard && npm run build`; the API serves it).

## External APIs: what they really return

**Ticketmaster Discovery v2** `https://app.ticketmaster.com/discovery/v2/events.json`
- Free tier: 5000 calls/day, 5/sec. Response headers `Rate-Limit`, `Rate-Limit-Available`.
- Deep paging cap: `page * size < 1000`.
- Presale *windows* are in `sales.presales[]` (name/start/end); presale *codes* are never included.
- Many names carry age-limit suffixes and NBSPs; some attractions have empty names; venue names are branded.
- Real Philly music volume: ~500 events in the next 240 days.

**Deezer** `https://api.deezer.com/search/artist?q=<name>&limit=10`
- No key. ~50 requests / 5 s. Errors come back as HTTP 200 with an `error` object (quota = code 4).
- Gives `nb_fan`. No genres on artist search results.

**Spotify Web API** (client credentials, this app is in development mode)
- Search/artist objects: only `external_urls, href, id, images, name, type, uri`. No popularity/followers/
  genres. `top-tracks` → 403. Effectively useless for ranking; not used.

## Cloudflare (set up 2026-10-21)

**This repo is public**: don't write account IDs, emails, other projects' names/domains, or the Zero Trust
team domain into these notes. Read them from the Cloudflare API when needed.

- Pages project **`philly-events`** → https://philly-events.pages.dev (production branch `main`). Created via the
  REST API. The account has other Pages projects and Access apps for unrelated sites: don't touch them.
- Access app **"Philly Events dashboard"** covers `philly-events.pages.dev` and `*.philly-events.pages.dev`,
  session 720h, using the user's existing **reusable policy "Admin Only"** (the user chose to reuse it).
  Recreate with `python -m tracker.publish --setup-access "Admin Only"`.
- Token (`CLOUDFLARE_API_TOKEN`) is account-scoped with Pages:Edit + Access Apps and Policies:Edit. It can NOT
  run `wrangler whoami` or read `/access/organizations` (403); neither is needed.
- Verified after the first deploy: main URL, `/data/*.json`, and the per-deployment `<hash>.` URL all 302 to the
  Access login.

## Useful commands

```powershell
.venv\Scripts\python -m pytest -q                       # 33 tests, ~1.5 s, no network
.venv\Scripts\python -m tracker.run --from-cache --no-drafts   # replay last pull, 0 API calls
.venv\Scripts\python -m tracker.run --no-drafts          # real fetch (~3 TM calls) + alerts
Start-ScheduledTask PhillyEvents-Dashboard; Stop-ScheduledTask PhillyEvents-Dashboard
Get-ScheduledTask PhillyEvents-* | Get-ScheduledTaskInfo
```

Scratch test DBs: set `TRACKER_DB=<path>` (and `DASHBOARD_PORT=8766` to run a second dashboard).
