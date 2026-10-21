# Philly Events → TikTok Content Pipeline

> A project brief, not a spec. Everything below is context and suggestions. Use your judgment, swap tools, restructure, or skip pieces that don't pay off. The goal matters more than the exact approach.

---

## The goal

I run a TikTok page and want to keep people in the loop on **what's happening in the Philadelphia area**, starting with **concerts**. Later it could expand to **Sixers/basketball, food events, festivals**, and other things to do.

I want a system on my mini PC that keeps track of upcoming events, notices when something worth posting about happens (new tour announced, presale coming up, a big weekend), and drafts video ideas and scripts I can review and film.

It's a content assistant, not an autopilot. I review everything before it goes out.

### Video formats I'm thinking of

- **"What's happening this weekend / this week"** in Philly
- **"Did you realize how stacked this month is for ___ genre"**
- **"Look out for the ___ presale. ___ is finally going back on tour after ___ years"**
- Generally: keeping people in the loop on what's happening and what to do, concert-wise first

New format ideas are welcome if the data makes them easy (e.g. "shows still under $40 this month," "sold out → resale price watch," "venue spotlight").

---

## My setup

- **Machine:** Windows 11 mini PC that also runs **Homebridge** (hb-service, Node.js) for my smart home. **Don't break or disrupt that.**
- **Already familiar with:** Python, Java, React + TypeScript, NestJS, Prisma, SQLite
- **Already used:** **Ollama** locally for caption generation, **Windows Task Scheduler** for scheduled Python jobs, `spotipy`, and Pillow for a Spotify "Now Playing" TikTok slideshow automation
- I'm comfortable reading and tweaking code, so clear structure and comments help more than heavy abstraction.

---

## Suggested approach (flexible)

### Core idea: the AI writes, it doesn't fetch

Structured APIs/scrapers pull event data on a schedule. A local database stores it and tracks what's new or changed. The local LLM only turns that data into scripts, captions, and on-screen text. That keeps facts accurate (no hallucinated dates) and keeps the model's job light enough for a mini PC.

```
[scheduled job, e.g. nightly]
        │
        ▼
 fetchers  ──►  database (events, artists, presales, seen/changed)
                     │
                     ▼
              detectors ("new tour", "presale soon", "stacked month", "this weekend")
                     │
                     ▼
              local LLM (Ollama) → script / caption / on-screen text drafts
                     │
                     ▼
              drafts folder or small dashboard → I review and film
```

### Possible data sources (verify current access/terms before relying on any)

| Source | What it's useful for | Notes |
|---|---|---|
| **Ticketmaster Discovery API** | Main backbone: concerts, Sixers, venues, on-sale + presale windows | Free developer key with a daily call limit. Presale *windows* appear in event data, but presale *codes* do not. |
| **SeatGeek API** | Backfill smaller venues and other listings | |
| **setlist.fm API** | "Last time they toured / played Philly" for the "back after X years" angle | |
| **Spotify API** | Artist genres and popularity for genre roundups and ranking | Spotify has restricted some endpoints in the past, so check what's still available. |
| **Venue sites / local calendars** (Visit Philly, venue pages, etc.) | Food, festivals, smaller events with no clean API | Scraping is fine where allowed. Treat it as a later phase. |

Philly-area venues to make sure are covered include arenas, stadiums, the Mann, and mid-size rooms (Fillmore, Union Transfer, The Met, Franklin Music Hall, etc.). A radius-based search around Philly (~30 mi) is probably simpler than maintaining a venue list.

### Detection ideas

- **New announcement:** an event/artist not seen before in the Philly area
- **Presale / on-sale soon:** windows opening in the next 24–72 hours
- **Return after a long gap:** new Philly date where the last tour or show was years ago
- **Stacked month:** many notable artists in one genre in the same month, weighted by popularity
- **Weekend roundup:** best events Fri–Sun, mixed across genres and price points
- Anything else that seems like a good hook

### LLM side

- Ollama with a small instruction-tuned model is likely enough. Pick whatever runs well on the hardware.
- Feed it **only structured facts** from the database plus a format template, and have it produce a hook, a short script, a caption, hashtags, and on-screen text.
- Tone should be casual, hype-y, and quick. Hooks matter most.

### Output

Whatever's easiest to review. Suggestions:
- Markdown drafts in dated folders, or
- A small local web dashboard (React/TS fits my stack) listing upcoming events, triggered alerts, and generated drafts with copy buttons
- Optional later: a phone notification when a big presale alert fires

---

## Rough phases (reorder freely)

1. Fetch Philly-area concerts from Ticketmaster into a local DB
2. Track new/changed events between runs
3. Presale/on-sale + weekend detectors
4. LLM drafting for the three core formats
5. Enrichment (setlist.fm history, Spotify genre/popularity)
6. Expand to Sixers, food, festivals, and other categories
7. Nice-to-haves: dashboard, notifications, slideshow/asset generation (could reuse ideas from my Spotify Now Playing project)

---

## Guardrails (the only firm ones)

- Don't interfere with Homebridge or anything else already running on the mini PC.
- Keep API keys in a `.env` or similar, out of code.
- Respect API rate limits and site terms. Cache aggressively.
- Facts (dates, venues, prices) come from data, never from the LLM's imagination.
- Nothing posts automatically. Drafts are for my review.

Everything else (language choices, folder structure, libraries, scheduling method, database design, UI) is up to you. If a better approach shows up along the way, go with it and leave a short note on why.

---

## Open questions to decide as you go

- How often to fetch (nightly vs. a few times a day around on-sale times)?
- Which genres to track first and how to label them?
- How to rank what's "notable" so the drafts focus on events people actually care about?
- What's the best review workflow for me day to day?