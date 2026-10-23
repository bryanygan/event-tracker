# Music news pillar: implementation roadmap

Built 2026-10-23 from outside advice the user received (a review of `main` dated 2026-10-22; the advice document
itself is not kept in the repo, so everything it said is captured here). Checked against the code as it stands
after the editorial calendar merge (`a69b46a`) and live probes (section 3). Every recommendation in the advice is
mapped to a phase below; section 13 is the checklist that proves it. The user's decisions of 2026-10-23 are in
section 12 and are already applied throughout.

Status vocabulary for each item: planned → in progress → implemented → validated, or explicitly deferred.
Everything here is **planned** unless marked otherwise.

---

## 1. Goal in one paragraph

Grow the tracker from "where artists perform in Philly" into "following artists' careers": releases,
announcements, collaborations and audience momentum. The Philly concert pipeline stays exactly as it is and
remains **one content pillar**. National music news becomes a **second, independent pillar**, discovered by its own
pipeline and fed into the same creator workflow (queue, drafts, slides, posts, analytics). Your listening history
is the personalization layer that keeps hundreds of articles a day down to the handful worth a video.

The lifecycle this enables (from the advice): an artist enters the platform because their album was announced,
becomes a recurring subject as singles gain traction, and later triggers a local event story when they announce a
Philly date. One artist identity links all three.

## 2. Principles carried over (non-negotiable)

These are the project's firm rules plus the advice's own rules. Every phase is checked against them.

1. **Data sources establish facts. Rules decide what deserves attention. AI helps with presentation.** (The
   advice's restatement of this repo's core principle; keep it.)
2. **Facts come from data, never the LLM.** A release date, title or tracklist in a draft must trace to a stored
   piece of evidence. Claude writes the words around verified facts and is fact-checked like event drafts are
   (`llm.check_facts`).
3. **Announced ≠ rumored ≠ teased ≠ scheduled ≠ released.** Every story carries one of these statuses, and
   scripts must say which. "A mysterious Instagram caption should never be silently promoted into a confirmed
   album announcement."
4. **Nothing posts automatically.** Stories become ideas you review; drafts are for your review.
5. **Respect API limits and site terms; cache aggressively.** RSS with conditional requests; no full-article
   scraping unless a site permits it; store metadata and short excerpts, not article bodies.
6. **Discover first, verify independently, then draft.** Discovery (RSS) and verification (catalogs, official
   sources) are separate steps, recorded separately.
7. **Keys in `.env`, never in code or git.** The repo is public: no listening-history data, no personal artist
   lists, no account IDs in commits; tests use invented artist names.
8. **Don't disturb anything else on the mini PC** (Homebridge on 8581, the slideshow dashboard on 8000). New jobs
   are light, run as their own scheduled task, and share the existing one-run-at-a-time lock.
9. **Keep costs low: free sources only for now** (decided 2026-10-23). No Apple Developer Program; the free
   iTunes Search API, Deezer, MusicBrainz, Last.fm and ListenBrainz cover catalog and discovery. Claude only for
   shortlisted stories; Perplexity only for strong ones.
10. **Its own tab now, part of What to post later** (decided 2026-10-23). The Music news tab stays separate while
    the pipeline proves itself; once it reliably pulls stories you'd actually post about, it merges into What to
    post (milestone M9, with criteria). Nothing nags about how much you post (your feedback on the calendar,
    2026-10-22).
11. **The first release deliberately excludes expensive social scraping and complicated prediction models.** A
    useful RSS-to-content-queue workflow is already a substantial improvement.

## 3. Where we start (baseline)

### 3.1 What already exists that the music pillar reuses

The advice confirmed these were implemented on 2026-10-22; all are still on `main`:

| Piece | Where | How the music pillar uses it |
| --- | --- | --- |
| Content Opportunity Score | `tracker/opportunities.py` | A second scoring path for music stories (M5) |
| Creator-first queue | `QueueView.tsx` | Promoted music stories appear here with a Music pill (M5) |
| Post analytics | `tracker/posts.py`, `PostsView.tsx` | New formats appear automatically; add a pillar grouping (M6) |
| Content angles | `tracker/angles.py` | New music angles/formats (M6) |
| Manual trend notes | `tracker/trends.py` | Match notes against music stories too (M5); for anything you spot by hand (TikTok sounds, a thread you read) |
| 9:16 slides | `tracker/slides.py` | Music slide manifests (M6) |
| Perplexity research | `tracker/context.py` | Research by story or artist, not only by event alert (M6) |
| Remote editing | Cloudflare Tunnel + Access | Works for the new tab unchanged (writes are audited the same way) |
| Midday check (11:30) | `run.py --midday` | Pattern for the news job's light schedule (M1) |
| Claude Haiku drafts, Ollama fallback | `tracker/llm.py` | Same path, same daily cap and cost log (M6) |

Added since the review: Deezer release-genre check (`fetchers/deezer.py` already calls `/artist/{id}/albums`,
which returns `release_date` and `record_type`), genre refresh in live alerts, `--redraft-local`, and the
editorial calendar tab (`planner.py`; music ideas will show there once promoted, with no extra work).

Personal data available (read-only, `LISTENING_DB`, never committed): `plays` (1,866 artists played in the last two
years; **350 with 25+ plays**) and `artist_genres` (`primary_bucket`, `raw_genres`, `lastfm_tags`,
`spotify_artist_id`). Buckets: hip-hop 941, pop 293, electronic 280, trap 103, r&b 86, indie 65, melodic-rap 62,
drill 44, plugg 30, and 1,444 unknown.

### 3.2 What's missing (the advice's key point)

A **discovery engine that creates a content opportunity even when the artist has no upcoming Philly concert**:
an album announcement, an unexpected collaboration, a rapidly growing underground artist. Perplexity can research
a story you already know about; it is not a discovery source and won't reliably find every announcement or
underground release.

### 3.3 Live probes (2026-10-23, one request each)

RSS feeds for the publications the advice recommends:

| Niche (from the advice) | Source | Feed checked | Result |
| --- | --- | --- | --- |
| Rap, trap, rage, plugg | HipHopDX | `hiphopdx.com/rss/news.xml` | ⚠ responds, but newest item is Apr 22 2026: stale, don't rely on it |
| Rap, trap, rage, plugg | Complex Music | `/music/rss`, `/feeds/channels/music.xml` | ✗ 404; needs another route (M0) |
| Rap / hyperpop / underground | The FADER | `thefader.com/feed` | ✓ 20 items, current, ETag |
| Hyperpop, experimental | Stereogum | `stereogum.com/feed/` | ✓ 40 items, current, ETag |
| Hyperpop, experimental | Pigeons & Planes | `/pigeons-and-planes/rss`, `pigeonsandplanes.com/feed` | ✗ 404 (it lives inside Complex now); M0 |
| R&B | Rated R&B | `ratedrnb.com/feed/` | ✓ 6 items, current, ETag + Last-Modified |
| R&B | COLORS | (a YouTube channel, not a publication) | → YouTube adapter (M8) |
| EDM, electronic | Mixmag | `mixmag.net/rss.xml` | ✓ 80 items, Last-Modified |
| EDM, electronic | Dancing Astronaut | `dancingastronaut.com/feed/` | ? timed out at 15 s; retry in M0 |
| EDM, electronic | Resident Advisor | `ra.co/xml/news.xml` | ✗ 404; no public feed found |
| Indie / bedroom pop | Pitchfork | `pitchfork.com/feed/feed-news/rss` (+ album reviews feed) | ✓ 30 items each, current (official RSS directory) |
| Indie / bedroom pop | The Line of Best Fit | `thelineofbestfit.com/feed` | ✓ 10 items, Last-Modified |
| Mainstream / general | Billboard | `billboard.com/feed/` | ✓ 10 items, current, ETag + Last-Modified |

The two rap sources in the advice are stale or missing, so on the user's request (2026-10-23) rap coverage was
researched separately:

| Rap source | Feed | Result |
| --- | --- | --- |
| XXL | `xxlmag.com/feed/` | ✓ 35 items, a week deep, current |
| HotNewHipHop | `hotnewhiphop.com/feed` | ✓ 10 items, under a day deep (needs the 30-minute schedule) |
| AllHipHop | `allhiphop.com/feed/` | ✓ 10 items, current |
| The Source | `thesource.com/feed/` | ✓ 49 items, current |
| Vibe | `vibe.com/feed/` | ✓ 10 items, current (rap + R&B) |
| Uproxx Music | `uproxx.com/music/feed/` | ✓ 50 items, but 575 KB and reaching back to April: mixed; conditional requests make it cheap |
| Rap-Up, Okayplayer, Elevator, Lyrical Lemonade, Audiomack World | various | ✗ no working feed found (Elevator and Lyrical Lemonade, the closest to rage/plugg/underground, are YouTube-first: M8) |
| Our Generation Music | `ogmusic.com/feed/` | ✗ connection refused |
| HipHopDX | `/feed` 410 Gone, `/rss/news.xml` stale | ✗ |
| Complex / Pigeons & Planes | no feed at any path tried | ✗ |
| Pitchfork, The FADER, Stereogum | (above) | ✓ also cover rap; The FADER and Stereogum lead on hyperpop/underground |

**Reddit (r/hiphopheads and others), checked 2026-10-23:** the `.json` listing endpoints return **403** to
unauthenticated requests. The `.rss` feeds still answer, but Reddit announced on 2026-09-30 that it **stops
supporting RSS on 2026-11-13**, stops accepting new public API requests after 2026-10-31, and closes public API
access by March 2027 (reported by Decrypt, TechWyse and others; see decisions.md). The Responsible Builder Policy
already requires approval for API access. So Reddit is **not** automated: building on a feed that disappears in
three weeks would be wasted work and against the direction of Reddit's rules. **Decided 2026-10-23: the rap
publication feeds below replace Reddit entirely** (no daily manual check either). If you happen to see something
on Reddit you can still log it as a trend note, but nothing depends on it.

**So the M1 source list is 13 feeds**, every niche covered: Pitchfork news (+ album reviews), Stereogum,
The FADER, Rated R&B, Mixmag, The Line of Best Fit, Billboard, XXL, HotNewHipHop, AllHipHop, The Source, Vibe,
Uproxx Music. Dancing Astronaut (EDM) timed out twice and is retried in M0. That's a little over the advice's
"roughly 8–12": M0's coverage numbers decide which ones earn their place.

**Checking every 30 minutes is within every source's limits** (user's request, 2026-10-23). No robots.txt blocks
any feed path; The FADER asks for a 10-second crawl delay and Billboard 5 seconds (one request per 30 minutes is
far under both); the feeds' own cache lifetimes are 1–15 minutes; 9 of 13 answer a conditional request with
**304 Not Modified**, so most checks transfer nothing. 13 feeds × 48 checks a day ≈ 620 requests a day, mostly
304s. Billboard, HotNewHipHop and AllHipHop only hold about a day of items, so 30 minutes is the right cadence for
them, not just a preference.

Release/discovery APIs:

| API | Probe | Key needed |
| --- | --- | --- |
| MusicBrainz | ✓ 200 | No; a descriptive User-Agent; ~1 request/second average |
| iTunes Search API (free, not in the advice; a candidate stand-in for the Apple Music API) | ✓ 200 | No |
| ListenBrainz | ✓ 200 | No for public stats |
| Last.fm | ✓ 200 with the key (`tag.getTopArtists` for "hyperpop" returned artists) | Yes: `LAST_FM_API_KEY` and `LAST_FM_SHARED_SECRET` are in `.env` (only the API key is needed for read calls) |
| Apple Music API | not used (decided 2026-10-23: free only) | Would need a paid Apple Developer Program membership; the free iTunes Search API stands in |
| Deezer | already in use | No |
| YouTube Data API | later (decided 2026-10-23) | Free key with a daily quota (M8) |
| Spotify | not used for this | `/browse/new-releases` is removed for Development Mode apps (2026 migration guide); keep Spotify out of the foundation |
| Reddit | not used | `.json` 403, RSS ending 2026-11-13, new API requests closed after 2026-10-31; the rap feeds replace it (above) |

---

## 4. Architecture

```
 Existing events pipeline (unchanged)         New music-news pipeline (M1-M4)
 Ticketmaster, venue calendars, local   ──┐   RSS feeds, catalog APIs (Deezer, MusicBrainz,
 events → events table → detectors        │   iTunes Search), official artist sources
                                           │        │
                                           │   normalize → match artists → classify story type
                                           │   → cluster articles into stories (keep every source)
                                           │   → verify against catalogs/official sources
                                           │        │
                                           │   music opportunity scoring (M5):
                                           │   relevance, urgency, momentum, confidence, content potential
                                           │        │
                                           └──► shared creator workflow: alerts → queue → drafts → slides
                                                → posts → analytics (+ Calendar tab, trend notes)
```

Decisions already settled by the advice:

- **Separate pipeline, shared dashboard.** Keep the events pipeline intact; add music news alongside it.
- **Don't force stories into the `events` table.** Album announcements have different timestamps, statuses,
  evidence and lifecycle rules than concerts. New tables (M1-M4).
- **Reuse `alerts` / `drafts` / `posts` first; migrate to a shared `content_opportunities` abstraction later and
  gradually, if ever** (M8). A promoted story becomes an alert of kind `music_story`; everything downstream already
  works on alerts.
- **Separate schedules.** News is more time-sensitive than listings; the news job runs on its own task.

---

## 5. Data model (all new tables; additive migrations through `db._ADDED_COLUMNS` / `CREATE TABLE IF NOT EXISTS`)

| Table | Key columns | Notes |
| --- | --- | --- |
| `news_sources` | id, name, kind (`rss`/`youtube`/`official`), url, niches (JSON), enabled, poll_minutes, etag, last_modified, last_fetched_at, last_ok_at, last_error, terms_note | `terms_note`: what the site allows and when it was checked. A broken source never deletes its articles (same rule as venue calendars). |
| `articles` | id, source_id, guid, url, title, excerpt (short), published_at, fetched_at, categories (JSON), title_hash | Metadata + the feed's own summary only; no article bodies. Unique on (source_id, guid). |
| `artist_identities` | id, name, aliases (JSON), mbid, deezer_id, itunes_id, lastfm_url, attraction_id (link to the events pipeline's `attractions`), tier (`core`/`adjacent`/`watch`/`breakout`), origin (`listening`/`manual`/`related`/`editorial`), muted, ambiguous_name, created_at | One identity per real artist across platforms (MusicBrainz's role). Lives in `data/tracker.db` only (gitignored). |
| `article_artists` | article_id, artist_id, match (`exact`/`alias`/`title_pattern`), confidence | How an article was tied to an artist. |
| `stories` | id, key, story_type, primary_artist_id, other_artist_ids (JSON), title, release_title, release_status (`rumored`/`teased`/`announced`/`scheduled`/`released`), release_date, verification (`unverified`/`corroborated`/`confirmed`/`conflicting`), first_seen, last_seen, sensitive, feedback, alert_id | One card per real-world story, however many articles cover it. `alert_id` once promoted to the queue. |
| `story_articles` | story_id, article_id | The cluster. Three publications on one album = one story with three sources. |
| `story_evidence` | story_id, kind (`article`/`catalog`/`official`), provider, url, excerpt, claim (`release_date`/`title`/`type`/`collaborator`/`status`), value, observed_at | Every factual claim with where it came from. Drafts read facts from here only. |
| `releases` | id, artist_id, provider, provider_id, title, record_type (`album`/`ep`/`single`), release_date, first_seen, last_seen | Catalog records per provider, kept side by side so missing or conflicting data is visible. |
| `artist_metrics` | artist_id, provider, metric (`fans`/`listeners`/`playcount`/`mentions`), value, observed_on | Daily snapshots for momentum (M7). Never back-filled or guessed: a missing day stays missing. |
| `story_feedback` (or columns on `stories`) | story_id, action (`interested`/`not_my_lane`/`already_covered`), at | Your editorial feedback (M5). |

The ingestion record from the advice maps onto these tables directly:

| Advice field | Stored as |
| --- | --- |
| Source: music publication | `articles.source_id` → `news_sources` |
| Artist: matched artist ID | `article_artists` → `artist_identities` |
| Story type: `album_announcement` | `stories.story_type` |
| Published: source timestamp | `articles.published_at` |
| Release date: needs confirmation | `stories.release_date` + `verification` + `story_evidence` |
| Evidence: article URL and excerpt | `story_evidence` (kind `article`) |
| Badge: Unverified | `stories.verification = 'unverified'` |

Pipeline runs reuse the `runs` table with a new source name (`news`), so the Runs tab shows them for free.

---

## 6. Phases

Sizes are rough (S ≈ a session, M ≈ 2–3, L ≈ 4+). Each phase is its own branch, tested, merged only with your
OK. Implementation order follows the advice's five steps (section 9 of it): RSS discovery → watchlist → release
metadata → verification + scoring → drafting + analytics. M0 comes first because the advice's closing point is
"prototype the news ingestion and verify real-world coverage… before investing in more complicated automation."

### M0: Coverage prototype (S, no app changes, cost $0)

**Why:** the advice's most important next step is checking real coverage, especially for smaller rap,
hyperpop and alternative artists.

1. Start from the 13 feeds in section 3.3 (rap coverage already researched: XXL, HotNewHipHop, AllHipHop,
   The Source, Vibe, Uproxx). Retry Dancing Astronaut. robots.txt, crawl delays, cache headers and conditional
   requests are already checked; still read each site's terms for feed use and note them.
2. For each source record in a scratch file: item count, how far back it goes, whether excerpts are included,
   and the share of items that are music news (Vibe, Uproxx and The Source also publish non-music stories).
3. Run a throwaway script (outside the app code, in the gitignored `data/m0/`) **every 30 minutes** for about a
   week: conditional requests, item metadata into a scratch SQLite file, titles matched against your 350 core
   artists (25+ plays) at report time. No Claude.
4. Report: articles per day per source, the share that mention a watchlist artist, duplicates across sources (how
   many stories each article count collapses to), the story types seen, **coverage for small artists** (of your
   core artists who released something that week, from Deezer `/artist/{id}/albums`, how many showed up in any
   feed at all), and a sample of matched stories for you to judge: "would I have posted about this?".

**Acceptance:** a source list with terms notes, a coverage number for small artists, and a go/no-go per source.
If small-artist coverage is poor, that decides how much weight M2's catalog checks and M7's discovery carry.

**Rules check:** one request per feed per 30 minutes (within every source's limits, section 3.3), conditional
requests where supported, nothing committed except the findings (no listening data in the write-up).

### M1: RSS news discovery (M)

Advice step 01: "Start with roughly 8–12 quality sources rather than dozens. Normalize articles, deduplicate
stories, add article timestamps, and retain source URLs."

- `tracker/fetchers/news_rss.py`: fetch each enabled source with `If-None-Match` / `If-Modified-Since`; parse
  RSS 2.0 and Atom (pick one small, maintained parser, version pinned and at least two weeks old, or the stdlib
  `xml.etree` if the feeds are clean); normalize to (guid, url, title, excerpt, published_at UTC, categories);
  reject items older than N days on first fetch; store new rows only.
- Back off per source on errors (as venue calendars do: a failing source is reported, its old articles kept).
- Schema: `news_sources`, `articles` (section 5). Seed sources from a config list in `tracker/config.py` (URLs
  are public, fine in the repo).
- Scheduling: new task **`PhillyEvents-News`**, **every 30 minutes** around the clock (decided 2026-10-23;
  within every source's limits, section 3.3), running `python -m tracker.news`. Per-source `poll_minutes` (default
  30) lets one site be slowed if its terms or errors call for it, and a source that keeps failing backs off
  (30 → 60 → 120 minutes) until it recovers. The job takes the same run lock as the concert pipeline and skips
  quietly if a run is in progress (a skipped check is caught up 30 minutes later; the feeds hold at least a day).
  No Ollama, no Claude, no web lookups. It must finish in seconds.
- Dashboard: a minimal **Music news** tab (its own tab, last in the nav; it merges into What to post in M9)
  listing recent articles grouped by source, plus a Sources panel showing health (last OK, last error, item
  count, last 304).
- Tests (mocked HTTP, invented names): conditional request handling (304 = no work), Atom + RSS parsing, dedupe
  on guid and on URL, timezone-less dates, a broken feed not erasing stored items, the lock skipping politely.

**Acceptance:** an hour of feeds lands in the DB with no duplicates; a 304 costs nothing; the tab shows them.

### M2: Personalized artist watchlist and identity (M)

Advice step 02: "Seed it from `LISTENING_DB`, then add editable watchlist artists and adjacent-artist
recommendations. Include favorite genres without restricting discovery to them."

The three groups from the advice:

- **Core artists**: from `plays.db`, artists over a play threshold in the listening window (start with
  `ROTATION_MIN_PLAYS` = 25 → 350 artists today). Every major release, collaboration and official announcement
  by them is a candidate for your feed.
- **Adjacent artists**: overlapping genres, frequent collaborations, similar audiences or shared editorial
  coverage; candidates even if you've never played them. Seeded in M7; the tier exists from M2.
- **Breakout candidates**: artists with new evidence of growth (M7).

Work:

- Schema: `artist_identities`, `article_artists`. Seed `core` from `listening.load()` (reuse `artist_key`
  matching and the `artist_genres` bucket as the niche). Link to the events pipeline's `attractions` by name key,
  so a Philly date and a news story share one artist.
- Identity: resolve each core artist to a Deezer id (already fetched for headliners) and a MusicBrainz id
  (search by name, accept only an exact name match with one strong candidate; otherwise mark `ambiguous_name`).
  MusicBrainz at ≤1 request/second with a User-Agent; 350 artists ≈ 6 minutes once, then only new ones.
- Matching articles to artists: exact or alias match on word boundaries in the title, then the excerpt; common-word
  names (an artist called "Future" or "Free") need a release-type keyword next to the name or a second signal;
  `ambiguous_name` artists are never matched on the excerpt alone.
- Editable watchlist in the Music news tab: add an artist (it resolves ids), set tier, mute, add aliases. Your
  edits override the seed and survive re-seeding.
- **Favorite genres without restricting discovery:** niche buckets boost relevance (M5) but never filter stories
  out; an unknown-bucket artist with a big story still surfaces.
- Privacy: the watchlist lives only in `data/tracker.db`; nothing from `plays.db` goes into facts the LLM sees
  (the existing `listening.py` rule) or into the repo; tests use invented artists.

**Acceptance:** the 350 core artists are seeded with ids where unambiguous; articles from M1 show matched artists;
editing the watchlist persists across re-seeds.

### M3: Story classification, clustering and status (M)

Part of advice step 04 ("story verification"), plus section 6 of it (group articles into stories).

- `tracker/music_news.py`:
  - **Classify** each matched article into a story type with transparent rules on title + excerpt (verbs and
    nouns like announce/reveal/share/drop/release/tease/collab/feature/tracklist/deluxe/tour/chart), recording which
    rule fired. Types: `album_announcement`, `single_release`, `album_release`, `collaboration`, `tracklist`,
    `tease`, `chart`, `artist_development`, `tour` (handed to the events side: does it touch Philly?), and
    `other`. No LLM in the classifier for the first release (cost, explainability); an optional Haiku tie-breaker
    for low-confidence items later, under the existing daily cap.
  - **Release status** for every story: `rumored` (unsourced claims, "reportedly"), `teased` (artist hints, a
    mysterious post), `announced` (title and/or date stated by the artist, label or a publication citing them),
    `scheduled` (a dated catalog pre-release entry or an official date), `released` (in a catalog). Status only
    moves forward on evidence; a downgrade (date pushed back, title changed) is kept as `conflicting` evidence.
  - **Cluster** articles into stories: same primary artist + compatible type + overlapping release title (or no
    title) within a window (start with 72 hours, longer for album cycles). Every article stays attached as a
    source. Three publications on one album = one story, three sources.
  - **Sensitive stories** (deaths, arrests, lawsuits, health): flagged `sensitive`, never auto-promoted, no
    template hooks; you decide.
- Tests: the same announcement from three feeds becomes one story with three sources; a tease never reads as
  announced; a later article with a new date marks the story conflicting rather than overwriting the date.

**Acceptance:** the tab shows stories (not articles), each with its sources, type, status and why it was
classified that way.

### M4: Release metadata and verification (M)

Advice step 03: "Test Apple Music and MusicBrainz against a small sample of artists and actual announcements,
then retain the best-performing providers. Record when catalog data is missing or conflicting." Plus step 04:
"Require reliable source evidence for factual claims."

- Provider bake-off on ~20 artists (mix of core-large, core-small, hyperpop/underground) and ~10 real
  announcements from M1-M3. For each provider record: artist found, latest release correct, future/pre-release
  entries present, date accuracy, delay after release day. Candidates:
  - **Deezer** (already integrated, free): `/artist/{id}/albums` with `release_date`, `record_type`.
  - **MusicBrainz** (free, ~1 rps): release groups with original release dates; cross-platform identity.
  - **iTunes Search / Lookup API** (free, no key): album and song lookups by artist id; may list pre-orders.
    This is the free stand-in for the advice's primary pick, the **Apple Music API** (artists, albums, songs,
    release dates, charts, artist-album relationships, latest-release views), which needs a paid Apple Developer
    Program membership and is **not used** (decided 2026-10-23: free only). Revisit only if every free provider
    falls short in the bake-off.
  - Keep the best one or two; document the results in `decisions.md`.
- `tracker/fetchers/musicbrainz.py` (+ `itunes.py` if it earns a place): poll watchlist artists'
  catalogs **every 6–12 hours** (folded into the existing 6:30 AM and 4:30 PM runs, or the news job's
  twice-daily branch). Store into `releases` per provider.
- Catalog-only discovery: a new release by a core artist that no feed covered still becomes a story
  (`single_release` / `album_release`, status `released`, verification `confirmed` by catalog). This fills the
  small-artist coverage gap M0 measures.
- Verification states: `unverified` (one article), `corroborated` (two or more independent publications agree),
  `confirmed` (catalog listing or an official artist/label source), `conflicting` (sources disagree on date,
  title or type; both values shown).
- No API is a complete advance-release calendar: labels announce before catalogs list, and a pre-release catalog
  entry doesn't make every announced detail final. Both are monitored, and the evidence table keeps them apart.
- Tests: providers disagreeing → conflicting with both values; a missing catalog entry doesn't block an announced
  story; rate limits (MusicBrainz ≤1 rps) enforced by a client like `DeezerClient`.

**Acceptance:** bake-off results recorded; stories show verification with evidence per claim; catalog-only
releases appear for small artists.

### M5: Music opportunity scoring, the Music news tab, and feedback (M)

Advice step 04: "Adapt your Opportunity Score for national music content so it doesn't automatically penalize
stories that lack a Philadelphia connection." Plus section 6 (dashboard) and section 7 (thresholds).

- Scoring path in `opportunities.py` for `music_story` ideas, with the same explainable component layout and the
  same "unknown is left out, not zero" rule. The advice's five dimensions:
  - **Music relevance**: watchlist tier (core > adjacent > breakout > none), your plays (personal signal only,
    never shown to the LLM), niche bucket match. Replaces `local` for music, so no Philly penalty.
  - **Urgency**: freshness of the news (first article age), or the release date coming up (New Music Friday,
    an album dropping next week).
  - **Momentum**: M7's signal; unknown (left out) until M7 exists.
  - **Confidence**: verification state (confirmed > corroborated > unverified; conflicting caps the score).
  - **Content potential**: story type weight (an unexpected collaboration or a big album announcement over a
    chart note), plus `ready` (facts complete, draft clean), as today.
  - New weights get their own `OPPORTUNITY_RULES_VERSION` bump; they're guesses to tune against logged posts.
- **Music news tab** (its own tab until M9 merges it into What to post):
  - Three views from the advice: **For you** (watchlist stories, best first), **New releases** (catalog-confirmed
    releases this week; the feed for New Music Friday), **Discover** (adjacent and breakout artists, M7).
  - Story cards: sources count with links, status pill (rumored/teased/announced/scheduled/released),
    verification pill, evidence per claim, suggested format, and "research needed" when confidence is low.
  - Feedback buttons: **Interested**, **Not my lane**, **Already covered**. They adjust editorial relevance (e.g.
    "not my lane" lowers that artist's or story type's relevance) and are stored separately from listening
    taste, so "what I listen to" and "what my audience wants" don't get mixed up.
  - **Send to queue**: creates the `music_story` alert from verified evidence only. Automatic promotion of
    stories that clear both the relevance and confidence thresholds exists as a setting but stays **off** until
    M9: while the tab is separate, you decide what reaches What to post.
  - Every action you take here (Send to queue, the three feedback buttons, ignoring a story) is logged, because
    M9's merge decision is measured from it.
- Queue: stories you send show a Music pill and a pillar filter (events / music / all); they also show up on the
  Calendar tab with no extra work.
- Trend notes (`trends.py`) match music stories too, so a sound or search trend you noticed boosts the story.
- "Already covered" also checks logged posts: a story about an artist you posted about recently gets the same
  caveat the queue gives events today.

**Acceptance:** a national story with no Philly date scores on its merits; the tab shows grouped stories with
feedback buttons; Send to queue creates a working alert.

### M6: Drafting, formats, research and analytics (M)

Advice step 05: "Reuse your current Claude/Ollama workflow, slideshow renderer, and post metrics. Track which
music-news categories actually drive performance." Plus section 8 (formats).

- Formats, built first per the advice (hooks are templates, not claims that a story is trending):

  | Format | Example hook (template) | Purpose | Facts it needs |
  | --- | --- | --- | --- |
  | Album announcement | "This album announcement just made my week." | Timely news | title, date or "no date yet", status, sources |
  | New Music Friday | "Here's what actually deserves a listen this Friday." | Weekly repeat viewing (a series) | this week's confirmed releases from watchlist artists |
  | Upcoming albums | "These are the albums I'm watching next month." | Saves, anticipation | announced/scheduled releases next month |
  | Emerging artists | "If you like this sound, you need to hear these artists." | Discovery, follows | adjacent/breakout artists + why (evidence) |
  | Unexpected collaboration | "I did not see this collaboration coming." | Shares, discussion | both artists, release status |
  | Music recommendations | "If you like underground rap, try these three projects." | Community building | released projects in a niche |
  | Artist development | "Here's why people are suddenly talking about this artist." | Context, opinion | momentum evidence + coverage |

- `angles.py` / `templates.py` / `llm.build_prompt`: per-format guides; scripts **must state the release status**
  ("announced, no date yet", "rumored, not confirmed"). `llm.check_facts` gets music rules: a date or title not
  in the story's evidence is flagged; words like "confirmed", "out now" or "dropping Friday" are flagged when the
  status doesn't support them.
- **Your perspective is the product.** A generic AI recap of an album announcement is easy to replace; a creator
  who knows the artist, their previous projects and the scene is not. Drafts leave a clearly marked "your take"
  beat in the script, and the card shows context to help (previous releases from the catalog, your notes), never
  invented opinions.
- `context.py`: Perplexity research **by story or artist**, not only by event alert, under the existing caps, and
  **only for sufficiently strong stories** (above a score threshold or on demand). Its answers stay context for
  you, never facts for drafts.
- `slides.py`: manifests for album announcement, New Music Friday and upcoming-albums lists (text slides built from
  facts; artwork only where the existing image rules allow).
- Analytics: posts get a `pillar` (events/music) derived from the alert; `posts.analytics` groups by pillar and by
  music format, so you can see which music-news categories drive performance.
- Costs: drafts use the same Claude Haiku path and daily cap (about $0.0003 a draft today); New Music Friday adds
  one weekly draft.

**Acceptance:** each format drafts with status-accurate wording and zero fact warnings on a sample; slides render;
analytics split by pillar.

### M7: Artist discovery and momentum (L, experimental)

Advice section 4: "Finding artists who are becoming popular before everyone else talks about them is harder—and
potentially more valuable." "The key is measuring change over time, not simply overall popularity."

- **Adjacent artists** (weekly job): from Last.fm `artist.getSimilar` and `tag.getTopArtists` for your niche
  tags (hyperpop, alternative R&B, underground rap, plugg, rage…), MusicBrainz collaboration relationships, and
  artists that keep appearing in the same articles as your core artists. Land as `adjacent` candidates in the
  Discover view with the reason. Last.fm tag counts are candidate lists, **not proof that an artist is going
  viral**. Uses `LAST_FM_API_KEY` from `.env` (set and working; `LAST_FM_SHARED_SECRET` is only for write/auth
  calls, which we don't make). Cache each artist's tags and similar artists for a week and keep the request rate
  modest, per Last.fm's terms.
- **Daily snapshots** into `artist_metrics` for watchlist + candidates: Deezer `nb_fan`, Last.fm listeners/playcount,
  ListenBrainz listen and unique-listener counts (underground coverage varies; treat as supplementary), and
  editorial mention counts from M1.
- **Momentum model**, deliberately simple and labelled experimental: growth over at least three observations at
  least a week apart, a minimum base so tiny numbers don't produce huge percentages, and agreement across at least
  two signals (e.g. fans growing and new independent mentions) before an artist becomes a `breakout` candidate.
  A ranking by audience size would always favor the established artist; growth relative to an artist's own
  baseline is the point. No machine-learning prediction in this phase.
- Breakout evidence in the advice's terms: repeated mentions in independent publications, increasing attention
  across multiple periods, or new official releases attracting discussion.
- The **Discover** view shows candidates with their evidence and a suggested format ("Three artists to listen to
  before they blow up"), plus feedback buttons; accepting one moves it to `watch` or `adjacent`.

**Acceptance:** weekly candidates with reasons; momentum shown only when its sample-size rules are met; nothing
called "trending" without two signals.

### M8: Later and optional (each its own decision)

- **YouTube adapter** (later, decided 2026-10-23): official artist and label channels, COLORS for R&B, and the
  YouTube-first rap/underground outlets with no working site feed (Lyrical Lemonade, Elevator), for music videos,
  singles and announcements, via the YouTube Data API within its daily quota (channel uploads, not search), or
  channel RSS feeds where available.
- **Official artist/label sources**: label newsletters, artist sites with feeds, press pages; these are the
  strongest `confirmed` evidence.
- **Reddit**: not used (the rap feeds replace it, decided 2026-10-23). `.json` is already 403, RSS ends
  2026-11-13, and new API requests close after 2026-10-31, so there's no lasting automated route; revisit only if
  Reddit offers an approved one for this use. Never scrape around it.
- **Cross-pillar link**: when an artist with music stories announces a Philly date, the event alert shows their
  recent story history, and the story shows the Philly date (one `artist_identities` row joins both).
- **Time-sensitive alerts to your phone**: only for stories above both the relevance and confidence thresholds,
  and only once phone alerts come off hold.
- **`content_opportunities` abstraction**: migrate alerts gradually to a shared opportunity model if music and
  event ideas start needing different lifecycles in the queue. Not needed while `alerts` works.
- **LLM-assisted classification**: Haiku as a tie-breaker for low-confidence classifications, under the daily cap.

### M9: Merge Music news into What to post (S–M, when the numbers say so)

Your plan (2026-10-23): keep music separate until it's reliably pulling content you want to post about, then make
it part of What to post.

**When:** measured from the actions M5 logs, over at least two weeks of real use, for example:

- most of the stories at the top of For you are ones you mark Interested or send to the queue, and few are
  "Not my lane";
- you've posted several music videos that came from the tab;
- no story that turned out wrong (bad artist match, a tease drafted as an announcement) in that period.

The exact bar is yours; the tab shows the numbers (share sent/Interested vs Not my lane, posts from music stories,
corrections) so the call is easy to make.

**What changes:**

- Music stories are ranked in What to post alongside event ideas, on one comparable opportunity score (M5's music
  path and the event path tuned against your logged post results), with the pillar filter (events / music / all)
  and a Music pill.
- Automatic promotion of stories above both thresholds turns on (or stays a setting, your call), so you don't
  have to press Send to queue.
- The Music news tab either goes away or stays as the place to manage the watchlist, sources and the Discover
  view, with its story list folded into What to post.
- Nothing else in What to post changes: same stages, shortlist, Later, notes, Posted.

**Acceptance:** one ranked list with both pillars, no duplicate cards between the tab and the queue, and the
events-only view (filter) looks exactly like What to post does today.

---

## 7. Schedule (advice section 7, mapped to scheduled tasks)

| Source type | Advice frequency | Plan |
| --- | --- | --- |
| News RSS feeds | Every 30–60 min, where permitted | `PhillyEvents-News` **every 30 minutes** (decided; within every source's limits, section 3.3); per-source `poll_minutes` and back-off on errors |
| Official artist/label feeds | Every 30–60 min, if supported | Same task (M8) |
| Catalog metadata (Deezer, MusicBrainz, iTunes Search; the advice's Apple Music row) | Every 6–12 hours | Inside the 6:30 AM and 4:30 PM pipeline runs (M4) |
| Emerging-artist metrics | Daily snapshots | 6:30 AM run (M7) |
| Genre discovery / watchlist expansion | Weekly | Monday 6:30 AM run (M7) |
| Perplexity research | Only for strong stories | On demand or above a score threshold, existing caps (M6) |

The RSS scanner stays lightweight: fetch, conditional request, store new metadata, dedupe. Claude calls are
reserved for shortlisted stories. The jobs are independent of the concert pipeline and share its one-run-at-a-time
lock, so they never fight over the database.

## 8. Dashboard summary

- **Music news tab** (new, its own tab until M9): For you / New releases / Discover; story cards with sources,
  status, verification and evidence; feedback (Interested / Not my lane / Already covered); Send to queue;
  watchlist editor; sources health; the M9 readiness numbers.
- **What to post**: stories you send there get a Music pill and a pillar filter. No other changes until M9, which
  merges the music stories into this list.
- **Calendar**: promoted stories appear like any idea; New Music Friday can be a series with a weekly cadence.
- **Posted**: pillar and music format in analytics.
- Alerts & drafts, Events, Runs: unchanged (news runs appear in Runs via the shared `runs` table).

## 9. Content strategy notes from the advice

- Music news benefits from a broader range of storytelling than event-specific scripts.
- Keep Philly concerts as one pillar and national music news as an independent pillar; your fan interests connect
  both.
- The account's value is your perspective: understanding the artist, their previous projects and the scene.
- Always distinguish announced, rumored, teased, scheduled and released.

## 10. Costs (estimates)

| Item | Cost |
| --- | --- |
| RSS, Deezer, MusicBrainz, iTunes Search, ListenBrainz | $0 (rate limits respected) |
| Last.fm | $0 (free key) |
| YouTube Data API (M8) | $0 within the daily quota |
| Claude Haiku drafts for promoted stories | ~$0.0003 each; ~$0.10–0.30/month at a few music drafts a day, under the same 40/day cap |
| Perplexity for strong stories | ~$0.0016 per lookup, existing caps |
| Apple Music API | Not used (free only, decided 2026-10-23) |
| Feed polling at 30 minutes | $0; ≈620 requests a day across 13 feeds, mostly 304 Not Modified |

## 11. Risks and how the plan handles them

| Risk | Handling |
| --- | --- |
| Feeds miss small/underground artists | M0 measures it; M4 catalog checks catch releases no feed covered; M7 discovery |
| Thin rap coverage (HipHopDX stale, Complex without a feed) | Six working rap feeds found (section 3.3); M0 measures what they catch |
| Underground rage/plugg coverage (outlets are YouTube-first) | The FADER/Stereogum + catalog checks for your core artists (M4) + YouTube later (M8) |
| Reddit going dark for automation | Not used at all; rap publication feeds instead |
| Wrong-artist matches (common-word names, namesakes) | Word-boundary matching, `ambiguous_name`, MusicBrainz ids, a second signal required |
| A tease or rumor drafted as confirmed | Release status on every story; status-aware fact check; templates that state status |
| Duplicate ideas from many outlets | Story clustering; one card, many sources |
| Site terms / scraping | RSS only, excerpts only, terms noted per source, no full-article scraping |
| API policy changes (Spotify, Reddit) | Neither is a dependency |
| Noise overwhelms the queue | Separate tab until M9; only stories you send reach the queue; feedback buttons |
| Momentum false positives | Minimum samples, minimum base, two-signal rule, labelled experimental |
| Mini PC load | 30-minute lightweight job (seconds per run), conditional requests, shared lock, no Ollama in the news job |
| Personal data leaking | Watchlist only in the local DB; nothing from `plays.db` in facts, prompts or git |

## 12. Decisions

Made by the user on 2026-10-23 (applied throughout this document):

1. **Apple Music API: no.** No Apple Developer Program; free sources only for now (iTunes Search, Deezer,
   MusicBrainz, Last.fm, ListenBrainz). Revisit only if all of them fall short in M4's bake-off.
2. **Last.fm: key saved** in `.env` as `LAST_FM_API_KEY` (plus `LAST_FM_SHARED_SECRET`, unused for read calls).
   Verified working 2026-10-23.
3. **Rap sources:** the user suggested r/hiphopheads (`.json` for new/top posts) and asked for better, more
   reputable sources. Result: Reddit can't be automated (`.json` 403, RSS ending 2026-11-13, API closing), and the
   user chose the publication feeds instead of a manual Reddit check; six working rap feeds (XXL, HotNewHipHop, AllHipHop,
   The Source, Vibe, Uproxx Music), alongside The FADER, Pitchfork and Stereogum (section 3.3).
4. **Feeds every 30 minutes**, since every source's limits allow it (section 3.3).
5. **YouTube: later** (M8).
6. **Separate tab for now, merged into What to post once it reliably pulls content worth posting** (M9).
   Automatic promotion stays off until then.

Still open (decide when the phase comes up):

- **M0:** whether every one of the 13 feeds earns its place, from the coverage numbers.
- **M9:** the exact readiness bar for the merge.

## 13. Coverage checklist: every recommendation in the advice → where it lands

| Advice (section) | Phase |
| --- | --- |
| Build a personalized music-news intelligence system on top of the tracker, not a separate app or one news API (intro) | Whole plan; shared DB and dashboard |
| Four source types: journalism/RSS, catalog APIs, communities/trend signals, official sources (intro) | M1, M4, M7 + trend notes, M8 |
| Listening history as the personalization layer (intro) | M2, M5 |
| Repo already has the features in the review's table (§1) | Section 3.1 (reused) |
| Missing: a discovery engine independent of Philly events (§1) | M1–M5 |
| Keep the events pipeline intact; separate news pipeline, shared dashboard (§1, §5) | Section 4 |
| Don't rely on Perplexity for discovery; use it for context (§2) | M1 (RSS), M6 (research) |
| Publication list by niche; prioritize The FADER, Stereogum, Pigeons & Planes (§2) | Section 3.3, M0 |
| RSS first; validate every feed; Pitchfork's official RSS directory (§2) | Section 3.3, M0, M1 |
| No reliable RSS → newsletter, permitted API or periodic review; don't assume scraping is allowed (§2) | M0, principle 5 |
| Ingestion record: source, artist id, story type, published, release date (needs confirmation), evidence, unverified badge (§2) | Section 5 mapping |
| Discover first, then verify before drafting (§2) | Principle 6, M3–M4 |
| Collect hundreds of articles, elevate only matching interests (§2) | M2 matching, M5 scoring |
| Apple Music API for artist/album/song/date/charts, latest releases; needs auth (§3) | Declined (free only, decision 1); iTunes Search stands in, M4 |
| MusicBrainz for identity, release groups, original dates; ~1 rps (§3) | M2, M4 |
| Last.fm tags and top artists by tag for niche discovery; not proof of virality (§3) | M7 (key set, decision 2) |
| ListenBrainz listen/unique listener counts as an experimental signal (§3) | M7 |
| No API is a complete advance-release calendar; monitor announcements and metadata (§3) | M4 |
| Don't build around Spotify (§3) | Section 3.3 |
| Core / adjacent / breakout artist groups; measure change over time (§4) | M2, M7 |
| Momentum needs consistent measurements, minimum samples, multiple observations (§4) | M7 |
| Reddit manual only unless approved under the Responsible Builder Policy (§4) | Not used: rap feeds replace it (section 3.3, decision 3), M8 |
| (User, 2026-10-23) Separate tab now, part of What to post once reliable | M9 |
| Monitor artist/label YouTube channels within quota (§4) | M8 |
| "Data establishes facts, rules decide attention, AI presents" (§5) | Principle 1 |
| Flow: normalize, match, dedupe, verify; group articles and keep evidence; score on relevance, urgency, momentum, confidence, content potential; shared queue (§5) | M1–M5 |
| Codebase additions: `news_rss.py`, `apple_music.py`, `musicbrainz.py`, YouTube; `db.py` tables; `music_news.py`; `music_discovery.py`; opportunities path; context by story; angles; Music News view (§5) | M1, M4, M8; section 5; M3; M7 (`music_discovery.py`); M5; M6; M6; M1/M5 |
| Don't force stories into `events`; reuse alerts/drafts/posts or migrate to `content_opportunities` gradually (§5) | Section 4, M8 |
| Group articles into stories; one card with three sources (§6) | M3 |
| Music News views: For you / New releases / Discover; cards like "Emerging artist · research needed" (§6) | M5, M7 |
| Feedback: Interested, Not my lane, Already covered; don't mix your taste with your audience's (§6) | M5 |
| Separate schedule; frequency table; lightweight RSS with conditional requests; Claude only for shortlisted stories; shared lock (§7) | Section 7, M1 |
| Alerts only above relevance and confidence thresholds (§7) | M5 (queue), M8 (phone, when off hold) |
| Seven formats with template hooks; hooks aren't trend claims (§8) | M6 |
| Your perspective is the value, not a generic AI recap (§8) | M6 ("your take" beat), section 9 |
| Distinguish announced, rumored, teased, scheduled, released (§8) | M3, M6, principle 3 |
| Implementation order 01–05 (§9) | M1, M2, M4, M3+M5, M6 |
| First release excludes social scraping and complex prediction (§9) | Principle 11, M7 kept simple, M8 |
| Start with RSS, Apple Music, MusicBrainz, Last.fm, Perplexity (§10) | M1, iTunes Search instead of Apple (M4), M2/M4, M7, M6 |
| Two pillars; your fan interests connect them; the artist lifecycle example (§10) | Sections 1, 9; M8 cross-pillar link |
| Prototype ingestion and verify real-world coverage first, especially small rap/hyperpop/alt artists (§10 closing) | M0 |
