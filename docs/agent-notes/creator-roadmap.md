# Philly Events Tracker — Creator Strategy & Implementation Roadmap

> **Purpose:** An implementation-ready handoff for an AI coding agent improving [`bryanygan/event-tracker`](https://github.com/bryanygan/event-tracker). Preserve the creator strategy, proposed features, tradeoffs, and cautions in the October 21, 2026 review. This document is **a proposal, not a description of already-shipped functionality**. Verify the current branch and code before changing anything.
>
> **Prepared:** October 21, 2026  
> **Primary user:** A Philadelphia-based TikTok content creator  
> **Primary goal:** Make the tool measurably better at finding promising local content, packaging it for TikTok, streamlining production, and learning from posted videos.  
> **Editorial policy:** Assist discovery, ranking, drafting, and review. **Never auto-post. The creator must approve factual claims, visuals, and publication.**

---

## 0. Executive direction — read this first

The existing application is already good at **detecting what is happening in the Philadelphia area**. Its next evolution should be a **creator operating system** that answers five questions:

1. **What's worth posting about right now?**
2. **Why should *this creator's audience* care?**
3. **Which hook and presentation angle will earn attention, saves, or shares?**
4. **What assets and verified facts are needed to make the post quickly?**
5. **Which published posts performed well, and what should the next recommendations learn from them?**

Do **not** mistake event notability, celebrity size, or a high existing score for predicted virality. A well-known artist at a large venue might be old news, while a rising artist at a small venue may be the more interesting and timely story.

### The immediate three priorities

1. **Creator feedback loop:** Store published-post records, metrics, and the originating event/opportunity/draft.
2. **Content Opportunity Engine:** Rank possible *videos* separately from ranking *events*, accounting for freshness, demand, surprise, relevance, and creative readiness.
3. **Creator-first content queue:** Surface the strongest actionable ideas, multiple hooks, production status, time sensitivity, and explicit explanations.

These three outrank adding lots of event scrapers, building a sophisticated automatic editor, or implementing phone push notifications.

### Guiding product principles

- **Creator-first, not data-first.** The most important screen is *what should I post?*, not *what rows were fetched?*.
- **AI writes and suggests, but structured sources determine facts.** Do not let the model invent artist names, event dates, venues, prices, presale codes, attendance figures, trend statistics, or ticket availability.
- **Everything should be explainable.** Show why an idea was ranked highly, and when supporting evidence was collected.
- **No invented virality statistics.** Estimated appeal and score weights are experimental heuristics, not scientifically validated forecasts.
- **An unknown signal is unknown, not zero or confidently negative.** Distinguish missing data from observed low popularity.
- **Keep costs and CPU use low.** The deployment machine is an Intel N95 Windows mini PC running other services, including Homebridge. Avoid persistent high-memory processes and unnecessary scraping.
- **Local-first and privacy-conscious.** Personal listening history must stay local. Keep the currently read-only hosted snapshot protected; no exposed write endpoints without robust authentication and authorization.
- **Do not disrupt existing functionality.** Tests, incremental SQLite migrations, backward compatibility, non-destructive changes, and reviewable pull requests are essential.
- **Treat social trends as perishable.** Every calendar-specific example and trend in this document is illustrative unless freshly reverified.

---

## 1. Repository understanding and current baseline

**Repository:** https://github.com/bryanygan/event-tracker  
**README:** https://github.com/bryanygan/event-tracker/blob/main/README.md  
**Project brief:** https://github.com/bryanygan/event-tracker/blob/main/event-tracker-pipeline.md  
**Existing agent handoff:** [`docs/agent-notes/`](https://github.com/bryanygan/event-tracker/tree/main/docs/agent-notes)

### What it does today (based on the inspected October 21, 2026 code/repo notes)

1. Fetches **Ticketmaster Discovery API** concerts within roughly 30 miles of Philadelphia City Hall, looking up to 240 days ahead, plus Sixers basketball events.
2. Augments venue coverage with **eight venue calendars**, including Underground Arts, MilkBoy, Ardmore Music Hall, Warehouse on Watts, The Ave, Johnny Brenda's, Kung Fu Necktie, and PhilaMOCA.
3. Enriches artist prominence using **Deezer** fan counts, appearance/tour information, and venue-size tiers; uses **setlist.fm** for an artist's previous Philadelphia-area appearance where known.
4. Optionally uses the user's own **listening-history SQLite database** in read-only mode for niche relevance; the actual play history is not committed or inserted into the model's prompt.
5. Stores events, presale windows, and append-only changes in **SQLite**.
6. Detects new Philly shows, new dates, presale/on-sale windows, returns after long absences, weekend roundups, stacked genre months, status changes, Sixers home weeks, and marquee games.
7. Scores event *notability*, then creates short TikTok hooks, scripts, captions, hashtags, and deterministic on-screen text through **Ollama / Qwen2.5:3b**, local templates, and fact checks.
8. Exposes a **FastAPI + React / TypeScript** dashboard with **Alerts & drafts**, **Events**, and **Runs**.
9. Allows manual editing and draft review (`pending`, `approved`, `filmed`, `rejected`), but **does not auto-publish**.
10. Can publish a protected **read-only Cloudflare Pages snapshot** for mobile browsing.
11. Schedules two daily pipeline passes, roughly **6:30 AM and 4:30 PM Eastern**, through Windows Task Scheduler.

### Current notability scoring

Inspect `tracker/scoring.py` and `tracker/config.py`. The baseline formula uses:

```text
Event notability = 0.50 × artist popularity
                  + 0.30 × venue tier
                  + 0.20 × tour-size signal
                  + optional personal-listening/niche bonuses
```

Other current thresholds/selection decisions include:

- `PRESALE_WINDOW_HOURS = 72`
- `PRESALE_MIN_SCORE = 45`
- `NEW_TOUR_MIN_SCORE = 35`
- `STACKED_MONTH_MIN_EVENTS = 5`, `STACKED_MONTH_MIN_SCORE = 50`
- `WEEKEND_TOP_N = 8`, maximum two weekend picks per genre
- `COMEBACK_MIN_YEARS = 3`
- Listening-history bonuses and a bypass for sufficiently frequent plays
- Draft generation prioritized by alert score

**Keep this notability system as one useful input, but do not overload it with content performance or engagement scoring.**

### Particularly strong engineering decisions — preserve them

- Initial fetches establish a baseline rather than falsely claiming that every existing event was newly announced.
- Structured data supplies facts; the model is not allowed to fetch or independently determine them.
- Dates and other on-screen text are generated deterministically, with the LLM given already-formatted facts.
- Presales and several status/game scripts are templated to reduce LLM error.
- A fact-checking tripwire flags suspicious numerical claims, times, dates, made-up codes, and leaked placeholders; creator review remains required.
- Venue-calendar fetch failures preserve previously known events rather than marking all of them removed.
- Ticketmaster duplicates with venue-calendar listings are handled; Ticketmaster's copy typically wins.
- The local model is started when drafting and stopped afterward to protect RAM for other processes.
- No automatic TikTok publishing is performed.
- The repository has mocked-network tests and explicit agent notes about prior mistakes.

### Important code entry points

| Concern | Existing file(s) |
|---|---|
| Environment/configuration and ranking constants | `tracker/config.py` |
| Event notability ranking and common event view | `tracker/scoring.py` |
| Alert detectors and keys | `tracker/detectors.py` |
| Event/change persistence | `tracker/store.py`, `tracker/db.py` |
| Local LLM prompts and fact checking | `tracker/llm.py` |
| Deterministic scripts/captions | `tracker/templates.py` |
| Draft creation, retries, and scheduling | `tracker/drafts.py`, `tracker/run.py` |
| API endpoints and edits | `tracker/api.py` |
| Front-end dashboard | `dashboard/src/App.tsx`, `dashboard/src/views/AlertsView.tsx`, `EventsView.tsx`, `RunsView.tsx`, `dashboard/src/api.ts` |
| Protected mobile snapshot | `tracker/publish.py` |
| Windows scheduled jobs | `scripts/install_tasks.ps1`, `scripts/uninstall_tasks.ps1` |
| Existing tests and operational limitations | `tests/`, `docs/agent-notes/status.md`, `decisions.md`, `gotchas.md`, `venue-coverage.md` |

**Agent onboarding requirement:** Read the README plus the relevant notes in `docs/agent-notes/` before implementing. Check current files and branch, because the inspected snapshot is not a guarantee of current machine state.

---

## 2. Audience and editorial positioning

### Two viable directions

**Recommended starting identity: Music-first Philly discovery.** Establish a distinctive voice for younger Philadelphia concertgoers: rap, R&B, EDM, hyperpop, indie/bedroom pop, rising artists, venue knowledge, and carefully selected mainstream announcements. Creator personality and informed selection should be visible.

**Later expansion: Broader "What's happening in Philly" coverage.** Test food festivals, Halloween/seasonal activities, student-friendly events, pop-ups, free and inexpensive outings, sports, and local experiences once the music content workflow is repeatable. This broadens the addressable audience but increases competition and data maintenance.

**Do not become a generic event-listing feed.** Viewers should follow because this creator filters, explains, and recommends—not because the page has a raw list of dates.

### Content pillars: six proposed recurring formats

| Format | Example *hook*, not a claimed current event | Core ingredients | Primary hypothesis to test |
|---|---|---|---|
| **1. Unexpected / breaking concert announcement** | "Nobody's talking about who's coming to Philly." | Freshness, surprising artist–venue pairing, return after years, verified onsale | Reach, shares, artist-fan discovery |
| **2. Genre-specific stacked-month slideshow** | "If you listen to R&B, November in Philly is insane." | 4–6 strong artists, dates, visually consistent slides; separate rap/R&B/pop/alternative cuts | Saves, tagging friends, repeatable series |
| **3. Things to do when bored in Philly** | "You have no excuse to stay home this weekend." | Mix concerts, food, markets, festivals, unique activities; dates/locations | Broad local discovery, shares |
| **4. Rising artist / "see them before they blow up"** | "This artist could be playing arenas next year." | Real upcoming small-room show, documented momentum, editorial explanation; avoid presenting speculation as certainty | Niche audience growth, follows, shares |
| **5. Presale / ticket reminder** | "Philly, these tickets go on sale tomorrow." | Exact timezone, verified opening window and official ticket link, clear distinction between presale and public onsale | Saves, repeat visits, reliability |
| **6. Local opinions / recommendations** | "Three Philly shows I'd pick over an arena concert." | Personal taste, comparisons, practical tradeoffs, clear reasoning | Comments, audience loyalty, repeat views |

### Format considerations

- **Announcement format:** Publish only when there is a genuinely new development. "Just announced" must be supported by the data and timestamp.
- **Stacked-month format:** Prefer targeted mini-roundups over a bloated cross-genre list. Generate multiple cuts when appropriate.
- **Weekend format:** A list of eight names may be overwhelming. Experiment with **3–5 compelling picks** per video, with alternative genre-specific or price-specific versions. The current eight-event cap can remain a source pool.
- **Emerging-artist format:** Do not imply predicted fame as fact; describe verifiable momentum, and label judgment as opinion.
- **Ticket format:** Be useful and exact. Don't invent sale status, presale codes, inventory, or resale prices. Do not make every presale a separate TikTok.
- **Local opinion format:** Explicitly invite a creator's personal perspective instead of making everything sound like an AI-written announcement.
- **Call to action:** Match the content: save a calendar, tag a concert friend, share a weekend plan, comment on a lineup—not the same generic CTA every time.
- **Hooks:** Support multiple candidate hooks, including short curiosity-based and informative alternatives. Hook variation should never change the underlying facts.
- **Human visuals:** Authentic footage, first-hand reaction, personal voice and editorial recommendations can differentiate the account from template-heavy aggregator pages.

### Trend strategy: don't chase sound-only virality

Distinguish three things:

1. **Trending sound/format:** A video/editing aesthetic or audio pattern popular on TikTok now.
2. **Trending topic/search:** An artist, festival, ticket announcement, event type, or local question people actively care about.
3. **Durable searchable demand:** "Things to do this weekend in Philadelphia," "concerts in Philly this month," "free events," "date ideas," and similar recurring needs.

The stronger general play is **local relevance + current audience demand + timing + compelling framing**, not inserting random trending hashtags or audio unrelated to a post.

TikTok's 2026 trend discourse emphasized authentic, less-polished expression and purposeful discovery/search. **Treat this as strategic context rather than an unchanging algorithm rule.** Validate current distribution and format trends before relying on them.

---

## 3. Temporal examples from the original October 21, 2026 review

These were cited as **potential editorial leads**, *not* automatically approved scripts or permanently correct event data:

- Philadelphia Halloween and late-October activities: timely roundups of haunted attractions, nightlife, seasonal markets, and festivals; a natural opportunity to broaden beyond concerts.
- **Arcángel — October 24, 2026:** potential Latin/reggaeton weekend angle.
- **Young Miko — November 4, 2026:** potential early-November Latin-music roundup.
- **Olivia Rodrigo — November 7–8, 2026:** potential pop calendar or fan-focused roundup.
- A general **late-October/November Philadelphia concert roundup**, segmented by genre.
- A late-October 2026 TikTok Halloween trend note involved the Róisín Murphy track *"Ramalama (Bang Bang)"* and costume-walk content, potentially adaptable to event imagery **if still trending and if appropriate music usage rights exist**.

**Do not hard-code these as live recommendations.** A coding agent should independently reverify dates, events, venue/price/sale details, and present relevance with current reliable sources (ideally official promoters/venues) before surfacing them to a creator. By the time the tool is implemented, they may be past events.

**General implementation lesson:** trend data needs `observed_at`, `source`, `scope/region`, and freshness/expiration checks. A topic that was viral a week ago is not necessarily still viral.

---

## 4. New feature: Content Opportunity Engine (P0)

### Why

The current score answers **"Is this event notable?"** but not **"Is there a compelling, timely video idea about it?"**. Separating these concerns unlocks breaking news, niche discovery, roundup ideas, personal commentary, and learning from outcomes.

### Two different objects

- **Event:** An actual show/game/activity, with source-backed facts and existing notability score.
- **Content opportunity:** A potential video *about one or more events*, with a format, angle, explanation, urgency, evidence, scoring features, possible hooks, production status, and lifecycle.

Multiple opportunities may point at one event (news video, presale reminder, artist comeback). One opportunity may group multiple events (genre roundup, weekend plans).

### Proposed initial scoring weights (EXPERIMENTAL)

| Feature | Weight | Why it matters | Examples of evidence |
|---|---:|---|---|
| Audience interest | **25%** | Likely relevance and existing fan demand | Artist prominence, in-niche, documented local discussion, creator results |
| Timeliness | **25%** | New stories lose value quickly; some are event-date driven | Announcement detected time, sale opens, event proximity, hours until expiry |
| Surprise / story value | **20%** | Strong angle can outperform artist size | Multi-year return, unexpected venue, rapidly rising act, unusually dense genre month |
| Philadelphia relevance | **15%** | Matches the account's geographic and audience focus | Philly-area venue, regional interest, audience niche |
| Search/content gap | **10%** | Chance to answer a searched question with relevant content | Current search observation and credible assessment of competing content |
| Production readiness | **5%** | Can the creator make a reliable, appealing post quickly? | Verified dates, trusted link, available permitted media, structured content template |
| **Total** | **100%** | | |

Suggested shape:

```text
opportunity_score = Σ(weight_i × normalized_observed_feature_i)

Display separately:
  event_notability_score
  opportunity_score
  evidence_completeness / confidence
  urgency / publish-by recommendation
  human-readable rationale
```

**Do not silently penalize missing observations as if they were negative facts.** Early implementation can use a small confidence/evidence-coverage indicator and score only what is known, making the fallback explicit. Validate missing-data behavior with tests.

### Illustrative contrast

- Big arena event: notability 90/100, announced several months ago, no new angle → potentially low *current* opportunity.
- Smaller hyperpop show: notability 48/100, growing attention with a novel local angle and near-term date → potentially high opportunity.

The numbers above are illustrations, **not measured scores**.

### Required outputs

Each opportunity should provide:

- `headline/working_title`
- `kind` / proposed format
- linked event IDs (one or many)
- `notability_score` for reference and separate `opportunity_score`
- individual scoring components and **evidence timestamps**
- 2–4 concise reasons it might work; 1–2 caveats or unknowns
- audience segment(s) and a creator-relevance signal
- new/updated/expiring/past states and a recommended post window
- associated fact references and source URLs
- three possible hooks/angles and recommended production format
- draft/asset state, creator editorial decision, and posted outcome (if any)

### Architecture suggestions

Create a module such as `tracker/opportunities.py` (name is optional) with deterministic candidate generation and explainable scoring. It should consume existing event/alert facts; it must **not** require the LLM to determine whether an event is real. Make weights/configuration adjustable. Add unit tests for novelty, urgency, missing evidence, expiry, duplicate opportunities, artist return, and niche boosts.

### Non-goals initially

- A claim to predict views or virality with a universally reliable percentage.
- Expensive social scraping or continuous model execution.
- Replacing the existing event notability and genre/taste logic.
- Autonomously publishing based on the score.

---

## 5. New feature: published-post performance feedback (P0)

### Why

The system presently tracks alerts and draft statuses, but does not have a mature connection between **what was posted and what happened afterward**. Without this feedback, the prioritization weights remain guesses.

### Minimum useful data to capture

Create a post record linked to the original opportunity and draft (when available):

- TikTok URL or video identifier
- `published_at`, content category, format, chosen hook/angle, and optional creator notes
- source alert/opportunity/draft IDs and/or source event IDs
- video length and format (voiceover, slideshow, footage, face-to-camera, etc.) if known
- metrics by **observation timestamp**: views, likes, comments, shares, saves/favorites, and followers gained if available
- optional metrics when exposed reliably: average watch time, completion/retention, traffic source, audience demographics
- optional manual feedback: "great hook", "low retention", "too late", "would repeat", etc.

**V1: manual entry** in a simple dashboard form is enough. Import/automatic integration is an optional later step, subject to TikTok's actual supported APIs and terms. Do not scrape personal analytics without ensuring permission and reliability.

### Basic analytics

- Median and distribution of views by format (avoid relying on one viral outlier).
- Shares per 1,000 views; saves per 1,000 views; comments per 1,000 views.
- Follows per 1,000 views where available.
- Retention/watch time by comparable video length where supported.
- Views at the same age after publishing—compare 24-hour with 24-hour, not a day-old post with a year-old post.
- Performance by category, topic, hook style, urgency, visual format, and post timing.
- Performance of "rising artist" vs "big artist" topics.
- Mark under-sampled segments as inconclusive; don't train on 1–2 anecdotes or overfit one exceptional post.

### Suggested initial capture cadence

Record or import performance **24 hours and 7 days** after posting, plus a later snapshot only when useful. Missing measurements should be nullable, not fabricated. Avoid obsessive or unnecessarily frequent polling.

### Data model sketch — adapt to the existing SQLite migrations

```text
content_opportunities(
  id, kind, title, status, priority, score,
  score_details_json, rationale_json, source_evidence_json,
  first_seen_at, last_evaluated_at, expires_at,
  editorial_notes, created_at, updated_at
)

opportunity_events(opportunity_id, event_id)

content_angles(
  id, opportunity_id, angle_type, hook, pitch,
  format, selected, created_at
)

published_posts(
  id, opportunity_id, draft_id NULL, platform,
  post_url, published_at, content_format,
  chosen_hook, creator_notes, created_at
)

post_metric_snapshots(
  id, post_id, observed_at,
  views NULL, likes NULL, comments NULL, shares NULL,
  saves NULL, followers_gained NULL,
  avg_watch_seconds NULL, completion_rate NULL,
  source, notes
)
```

This is a *conceptual schema*, not a required exact DDL. Respect foreign keys, migrations, sensible indexes, and preservation of existing reviews/drafts.

### Learning loop

1. Creator reviews and posts content.
2. Creator links the post and adds performance snapshots.
3. Analytics view compares outcomes across similar posts.
4. System summarizes evidence ("genre roundups receive more saves relative to views") **only when supported by enough data**.
5. Adjust ranking weights and prompt examples in small, reviewable increments.
6. Validate against future posts, not just the same data used to choose weights.

**Do not let a small dataset automatically retrain the score or make inflated claims.** Manual calibration first.

---

## 6. New feature: Creator-first Content Queue dashboard (P0)

### Current limitation

The present top-level UI—**Alerts & drafts**, **Events**, and **Runs**—maps to the data pipeline. The creator needs a front page built around **decisions and production**.

### Recommended new default view: `Content Queue`

Proposed top-level indicators:

- New candidate ideas
- Time-sensitive / expiring opportunities
- Ready to review / ready to film
- Already posted this week
- Overdue opportunities or fact refresh required

A candidate card should show:

```text
[Strong / Medium / Needs Evidence]  [Time-sensitive: publish by ...]
TITLE / ARTIST / TOPIC
Format: breaking news / roundup / slideshow / opinion / etc.
Suggested hook
Why this has potential (2–3 evidence-backed bullets)
Notability: XX (existing measure) | Opportunity: YY (experimental)
Supporting facts / links / last checked
[Review] [Choose angle] [Generate draft] [Defer] [Dismiss]
```

For example, the prior review sketched:

- A pop-event roundup around Olivia Rodrigo's November 2026 Philadelphia shows.
- A seasonal Halloween roundup with a clear expiry window.

**These are mock UI examples, not automatically verified/current listings.**

### Suggested workflow states

`discovered → shortlisted → drafting → ready_for_review → approved → produced/filmed → posted → measured`

Also support `deferred`, `dismissed`, `expired`, and `needs_fact_refresh` where appropriate. Distinguish these from current alert/draft statuses rather than breaking the existing flows.

### Crucial workflow improvements

- Show multiple candidate angles **before** generating an expensive full LLM draft.
- Show why the opportunity was selected and which evidence is stale or missing.
- Allow quick user note, save for later, creator edit, dismiss, and rejection rationale.
- Detect when a similar idea was already posted (same artist/topic, recent window) to reduce repetitive content.
- Display last fetch timestamp and the deadline/urgency of presales and upcoming events.
- Keep exact source links visible for verification.
- Support a lightweight posting calendar so a stream of presale alerts doesn't crowd out planned series.
- Maintain a clear protected mobile reading experience; authenticated edits can be a later milestone.
- Preserve the existing Alerts, Events, Runs, and troubleshooting views for detailed inspection.

---

## 7. New feature: multiple hooks, angles, and drafts (P0)

### Why

The current LLM writes a largely single default format for each alert. But different formats can work for the same fact pattern: a breaking-news hook, a practical calendar/sale reminder, a fan-specific comment, or a discovery opinion.

### Proposed angles for selected opportunities

1. **Breaking news:** What's new? Why now?
2. **Fan-focused / emotional:** What makes the announcement surprising or exciting?
3. **Utility / recommendation:** Why save this, when should you act, who should attend?

Additional optional angles: comparison, comeback story, genre roundup, emerging-artist discovery, opinion/debate, budget-friendly (only if verified prices exist).

### Content package for the creator

- Three distinct candidate hooks (preferably each 12 words or fewer when spoken).
- Recommended TikTok format (voiceover, slideshow, direct-to-camera, footage montage).
- A short creative brief explaining the proposed first visual and expected value to viewers.
- One selected full script in a suitable runtime (existing default 20–45 seconds, adjust per format).
- Verified on-screen titles/details generated deterministically.
- Caption, sensible CTA, and relevant—not random—hashtags.
- Optional storyboard with slide-by-slide *text and asset requirements*.
- Source/evidence panel with dates/times/prices as captured.

### Safety / truthfulness

- The LLM may **suggest** punchy framing, not manufacture urgency, ticket scarcity, venue attributes, popularity changes, historical firsts, or numerical claims.
- New hooks should pass the same fact checker as existing scripts and captions.
- For templated presale/status/game content, continue using deterministic copy; the LLM should not rewrite sale dates/times.
- Separate **editorial opinions** ("I'd go to this") from factual statements ("tickets are selling fast"). Only include the latter if supported.
- Avoid clickbait the facts do not justify.

### CPU strategy

Generate cheap/rule-based angle candidates first and call Ollama **on demand for high-ranked or explicitly selected opportunities**, respecting the N95's limited resources. Use caching and limit concurrent generation. No background high-memory resident model.

---

## 8. Trend discovery and signal enrichment (P1)

### Three evidence sources to combine

1. **TikTok Creator Search Insights:** manually or through officially supported access identify search interests, common questions, and content gaps. Do not assume a public automation API exists.
2. **TikTok Creative Center / trend resources:** when legitimately accessible, observe relevant formats, hashtags, themes, and sounds. Global data may not represent Philadelphia-specific demand, and delayed/sampled public charts should be labeled accordingly.
3. **The creator's own post results and comments:** in the long run this is the strongest account-specific signal. Learn which formats get saves, shares, comments, followers, and repeat attention.

Other optional, terms-compliant context sources: credible event announcements, artist/venue websites, Google Trends or similar search-interest sources, artist release calendars, creator-supplied observations, and documented community discussion. Verify terms, API access, geographic scope, and reliability **before implementation**.

### V1 implementation: manual trend observations

Create a small trend-input interface with fields:

- `keyword` / related artist / event category
- `source`, `source_url` or screenshot reference if provided
- `observed_at` and optionally `expires_at`
- region/scoped audience (national, US, Philadelphia, niche)
- observed momentum or qualitative label ("rising", "stable", "uncertain")
- free-text rationale and evidence notes
- confidence/evidence quality

Match observations to relevant event names, artist aliases, genres, and content opportunities. Do not infer a numerical trend velocity when no comparable observations exist.

### Later automation

Only automate permitted, stable, well-documented sources. Use reasonable call budgets, caching, error isolation, attribution, rate-limit handling, explicit freshness windows, and monitored source health. Avoid TikTok scraping that violates terms or triggers unreliable data collection.

### Useful angle-specific signals

- Announcement age and whether an event is newly added/changed
- Long absence from Philly, verified through setlist.fm or credible organizer material
- Difference between regional and general popularity
- Emerging artist attention, where independently supported
- Demand for date-specific / seasonal content
- High genre density in a date window
- Under-covered questions ("what's happening this weekend?")
- Evidence that posts with similar topic/format perform on the creator's account

---

## 9. Event data, detector, and ranking refinements

These are tangible code-level issues based on inspected files.

### 9.1 Alert refresh and stale roundups — high priority

In `tracker/detectors.py`, `save_alerts()` uses an `INSERT OR IGNORE` pattern on a unique key. This effectively prevents duplicate re-creation, but also means an existing weekend/monthly alert may retain stale facts when event listings, statuses, or selection quality change.

**Implement a distinction between:**

- immutable raw event change history,
- stable *opportunity identity*,
- current computed *facts/evidence version*,
- editable user draft versions and review decisions.

Use idempotent upserts or versioning for current opportunity facts. Do **not** silently overwrite the creator's edited script. Instead, mark relevant drafts `needs_fact_refresh` and show what changed, with a safe re-draft option.

Acceptance: subsequent fetches can update a current roundup after an event cancellation/new addition, while preserving creator edits, prior draft versions, posting links, and auditability.

### 9.2 Weekend selection

`detect_weekend()` currently chooses up to eight events, mostly by notability, and caps two per genre. Keep this as a broad discovery pool, but add:

- 3–5 event creator-ready "best picks" version
- Genre-specific cuts when enough compelling events exist
- Music-only vs broader things-to-do versions once non-music events are supported
- Budget-based roundups **only when verified prices are available**
- Optional creator's personal picks for differentiating the page
- A warning if a source or venue calendar is stale

Avoid forcing a minimum genre balance if it makes the actual video weaker. A highly focused R&B or hyperpop slideshow is sometimes more useful than an artificially diverse lineup.

### 9.3 Venue and genre enrichment

Venue-calendar listings often lack Ticketmaster genre metadata; some end up in `Other`. Improve artist/genre enrichment using defensible sources and existing listening-history buckets where available. Track source/confidence, distinguish unknown genre from genuinely mixed genre, and protect against mismatches between similarly named acts.

### 9.4 Niche/discovery fairness

Retain artist listening-history boosts and notability, but prevent major-label popularity and large capacities from dominating the *content opportunity* list. Smaller-venue acts need a fair path via observed trend evidence, return stories, niche fit, or editorial selection.

### 9.5 Scheduling, urgency, and freshness

The current pipeline runs at roughly **6:30 AM and 4:30 PM ET**. Consider one **lightweight midday data/detector pass** to notice important morning announcements earlier. It can run with `--no-drafts` initially. Only add this after establishing scheduler reliability and safe API usage.

Any presale urgency must use real, timezone-aware opening timestamps and never display a past deadline as upcoming. Make the freshest observation and suggested publication window visible.

### 9.6 Known event-source gaps

Longer-term expansion can cover:

- Food markets, pop-ups, seasonal festivals, free local activities
- Event price coverage (currently incomplete in Ticketmaster)
- Additional sports teams if audience demand supports them
- Responsible venue spotlights and local "date-night" formats

Do **not** turn incomplete Ticketmaster prices into invented "under $40" claims. A price-based filter must disclose coverage and recency, and official fees may change totals.

---

## 10. Creative asset generation: 9:16 slides and production aids (P1)

### Goal

Reduce the time required to turn an approved opportunity into a polished, recognizable TikTok post.

### Initial useful deliverables

- Reusable **vertical 9:16** branded slideshow template(s)
- Genre-specific color/mood templates with consistent typography
- Intro/title slide with a compelling but accurate hook
- One event per slide (artist, event date, venue, and optional verified sale details)
- Closing save/share/comment CTA slide
- Exportable PNGs and optionally a storyboard or editable layout manifest
- Optional voiceover cue sheet and timed scene list
- Source/rights manifest identifying image and audio origin

**Do not flatten away editable source information.** Store a structured scene/slide manifest so corrections to dates/times can re-render outputs reliably.

### Integration opportunity

The existing creator has a related local TikTok slideshow project and already uses Python/Pillow and music/listening history. **Inspect that project only if authorized and available to the coding agent** and reuse suitable, maintainable design logic where possible. Don't assume its filesystem path exists on other machines. Avoid coupling the projects so tightly that a slideshow failure prevents event ingestion.

### Media copyright and platform considerations

Only use images, footage, audio, and music in ways the creator has permission to use. Do not treat online artist photos and popular songs as automatically reusable. TikTok's general and commercial-use music rules differ; verify current platform rights and intended account/commercial use. Build user-supplied/licensed image support and make attribution/usage notes visible where relevant.

### Scope control

First generate **correct, aesthetically consistent static slides**. Sophisticated video assembly, footage selection, motion design, or auto-posting should not block the feedback and ranking work.

---

## 11. Publishing calendar and creator workflow improvements (P1)

Suggested weekly editorial experiment: roughly **four to six quality posts**, balanced across:

- one recurring Philly/weekend/month genre roundup,
- one to two urgent news or ticket alerts,
- one emerging-artist discovery,
- one locally useful recommendation/opinion,
- optional extra topical content when a real newsworthy moment occurs.

Treat this as a testing starting point, **not a required quota**. Prioritize strong ideas over volume.

Add lightweight scheduling and planning:

- Planned publish date/time and time-sensitive cutoff
- Format/topic balance across the week
- Status progression from idea to posted
- A "too similar to recently posted" indicator
- A recurring series/template tag
- Scheduled reminders only when creator requests them
- Ability to deprioritize low-value presales when roundups or unique local stories deserve attention

**Avoid adding unauthorized automatic posting.** The human creator owns final creative decisions and publication.

---

## 12. Mobile dashboard direction and security (later milestone)

The existing Cloudflare Pages deployment is a **read-only** snapshot protected by Cloudflare Access. This is an advantage: reading on a phone is convenient and safer than exposing write controls.

Potential later improvement: authenticated mobile shortlist/approve/edit notes while away from the mini PC. **Do not** open FastAPI's localhost API to the public Internet or put an unauthenticated write API behind a public static website. Design identity, session, CSRF, authorization, audit logs, secure transport, and appropriate synchronization first.

Until then, a protected read-only queue, copied scripts, and clear sync timestamps may be sufficient.

---

## 13. Analytics and decision-making methodology

To avoid self-deception as the account grows:

- Set a baseline before changing prompts or ranking thresholds.
- Group results by comparable content formats, audience niches, and post ages.
- Prefer medians plus variability over averages distorted by one breakout video.
- Use reach, shares, saves, followers gained, and retention together; virality alone is not the objective.
- Evaluate practical business value: creator time saved, posting consistency, fact-error rate, and audience growth.
- Preserve a snapshot of what the opportunity engine knew *at selection time*. This supports analysis without hindsight leakage.
- Avoid overconfident "this will go viral" language; say "high-priority hypothesis" with reasons.
- Keep experimental weights editable and versioned; changes should be reversible.
- Maintain a clear distinction between measured performance and manually assigned or LLM-proposed scores.

### Suggested outcome KPIs

**Creator workflow:** median time from detection to approved draft, time to produce each video, % of useful suggestions, stale/duplicate idea rate, fact corrections required, posting consistency.

**Audience performance:** comparable-age views, shares per 1k views, saves per 1k views, comments per 1k views, follower conversion, completion/watch time if available.

**System quality:** reliable daily fetches, missing/stale source warnings, unexpected memory retention, draft hallucination/warning rates, API budget usage, successful deduplication, no loss of manual edits.

---

## 14. Four-week rollout plan

### Week 1 — Gather evidence before overbuilding

- Publish a mixture of announcements, weekend/genre roundups, emerging-artist ideas, and local recommendations.
- Log hooks, post format, date/time, linked event(s), and 24-hour/7-day results (manual notes are fine).
- Reverify current topical opportunities; do not rely on the dated October/November 2026 examples without fresh confirmation.
- Observe which ideas get saves/shares/repeat engagement rather than just raw impressions.
- Review the reliability of the two daily scheduled runs and source warnings.

**Exit:** initial baseline, test videos, reliable event data and a list of real workflow bottlenecks.

### Week 2 — Content queue and scoring

- Introduce the separate opportunity object and explainable score.
- Implement opportunity freshness/expiry and stable identity.
- Add a basic creator-first queue with select/defer/dismiss and human-readable reasons.
- Generate three cheap hook/angle options per strong idea, then full drafts on creator selection.
- Avoid losing existing alerts/draft statuses.

**Exit:** creator can open the dashboard and quickly select the next promising post.

### Week 3 — Production assistance

- Build an exportable 9:16 image/slideshow template.
- Include deterministic dates/venues/on-screen details and a rights-aware asset workflow.
- Save/export scene instructions and allow regeneration after corrections.
- Begin post linkage and manual metrics entry if not finished in Week 2.

**Exit:** at least one content format is demonstrably quicker to make without sacrificing accuracy.

### Week 4 — Measure and refine

- Compare performance by format, topic, hook style, post age, and urgency.
- Adjust scoring rules based on actual evidence, with small controlled changes.
- Test whether deeper genre cuts and shorter weekend roundups outperform generic lists.
- Review stale-alert handling, source freshness, optional midday pass, and system stability.
- Choose whether food/festival expansion is justified by audience response.

**Exit:** data-backed next iteration and a stable, creator-usable flow.

---

## 15. Prioritized implementation backlog with acceptance criteria

These are suggested GitHub issues/PR-sized work packages. Create branches and PRs as appropriate; **do not merge automatically**. Existing agent notes indicate the owner's preference is verified changes on branches with approval before merging.

### P0-01 — Data model for content opportunities

**Touch:** `tracker/db.py`, new `tracker/opportunities.py`, tests.  
**Implement:** event/opportunity distinction, links to multiple events, stable identity, evidence/rationale/score detail storage, freshness/expiry and statuses.  
**Acceptance:** deterministic identities; migration preserves existing events/alerts/drafts; supports one-to-many and many-to-one; unknown evidence handled explicitly; unit tests pass.

### P0-02 — Explainable opportunity ranking

**Touch:** new opportunities module, `tracker/scoring.py`, `tracker/config.py`, tests.  
**Implement:** 25/25/20/15/10/5 experimental dimensions, independent from notability, configurable weights, transparent scoring explanation, normalization and missingness, easy recalculation.  
**Acceptance:** high-notability stale events need not top the queue; niche timely stories can rise; no fabricated trends; stable regression tests; versioned weights.

### P0-03 — Creator-first queue and lifecycle

**Touch:** `tracker/api.py`, `dashboard/src/App.tsx`, `dashboard/src/api.ts`, new queue view, tests.  
**Implement:** prioritized cards, source verification, urgency, reasons, 3-angle preview, shortlist/defer/dismiss, status progression, recently-posted warning.  
**Acceptance:** existing Alerts/Events/Runs remain accessible; mobile responsive; no phantom writes in static mode; existing statuses remain intact.

### P0-04 — Published post linkage and manual metrics

**Touch:** `tracker/db.py`, `tracker/api.py`, dashboard new analytics/post form, tests.  
**Implement:** post linked to opportunity/draft, TikTok URL, published time, chosen hook, manual performance snapshots; support nullable metrics and observation timestamps.  
**Acceptance:** create/edit post; store 24-hour and 7-day snapshots; link back to original idea; no reliance on unauthorized platform API; migration safe.

### P0-05 — Format-level analytics / feedback loop

**Touch:** new analytics module/view, tests.  
**Implement:** age-normalized comparisons, shares/saves per 1k views, median views, category/hook-type cuts, evidence/sample-size indicators.  
**Acceptance:** avoids misleading comparison of differently aged posts; missing data safe; never claims causal/predictive power from tiny samples.

### P0-06 — Three editorial angles and efficient draft selection

**Touch:** `tracker/llm.py`, `tracker/templates.py`, `tracker/drafts.py`, draft UI and tests.  
**Implement:** breaking-news, fan-emotion, practical/utility hooks, selected one expanded to full script, same fact checker, no fact invention.  
**Acceptance:** three distinct usable angles for eligible ideas; correct presale windows remain templated; creator can select and edit; CPU/memory budget respected.

### P0-07 — Stale roundup refresh, no data loss

**Touch:** `tracker/detectors.py`, `tracker/store.py`, `tracker/db.py`, draft refresh UX, tests.  
**Implement:** re-evaluate dynamic grouped opportunities; distinguish refreshed facts from manual edits; flag expired or inaccurate drafts.  
**Acceptance:** cancellations and added shows appear in refreshed opportunities; prior creator edits and status/history preserved; repeated runs idempotent.

### P1-08 — Genre-specific and concise creator roundups

**Touch:** detectors/opportunity generation, template guides, configuration/tests.  
**Implement:** 3–5 best-pick story variant, niche-specific monthly/weekend cuts; optional broader roundup when activity data supports it.  
**Acceptance:** no arbitrary "Other" domination; factual dates/venues; no invented price filters; avoids duplicate video suggestions.

### P1-09 — Trend evidence intake

**Touch:** SQLite, API, dashboard, matching logic/tests.  
**Implement:** manually entered TikTok search/trend/source observations with timestamps, scope and confidence; relevant opportunity matching.  
**Acceptance:** stale evidence visibly expires; no unverified scraped statistics; national signals aren't misrepresented as Philly signals.

### P1-10 — Artist/venue metadata completeness

**Touch:** venue fetchers, scoring/enrichment, tests.  
**Implement:** defensible genre and artist matching for venue calendar acts; known vs unknown fields and reliability indicators.  
**Acceptance:** fewer events accidentally grouped as `Other`; no incorrect merging of namesakes; source failures remain isolated.

### P1-11 — 9:16 slideshow export and rights-aware media

**Touch:** new presentation/asset exporter and dashboard support; possibly authorized integration with existing slideshow project.  
**Implement:** branded image slide output, deterministic event data, structured editable scene manifest, permitted imagery/credit metadata.  
**Acceptance:** export consistent vertical slides, edit and rerender after date correction; no assumed right to copyrighted art or music.

### P1-12 — Lightweight midday detection pass

**Touch:** `scripts/install_tasks.ps1`, `tracker/run.py`, operational docs/tests.  
**Implement:** optional data/detector-only midday check.  
**Acceptance:** doesn't leave an Ollama runner in RAM; obeys budgets; respects ongoing tasks; reliable scheduler; no duplicate flood; can be disabled.

### P1-13 — Editorial calendar, repeat-series planning

**Status (2026-10-22):** implemented (`tracker/planner.py`, Calendar tab); see decisions.md.

**Touch:** opportunity status/schema, dashboard.  
**Implement:** planned posting time, series labels, balance by format/niche, already-covered topic alert.  
**Acceptance:** creator retains final control; no automatic publishing; historic posted records preserved.

### P2-14 — Expand sources to broader Philly experiences

**Touch:** new compliant event-source adapters, normalization, scoring, tests.  
**Implement:** carefully chosen food events, markets, pop-ups, seasonal festivals, inexpensive/free experiences, potentially other sports.  
**Acceptance:** source permission and maintenance documented; event provenance preserved; venue outages don't erase data; unsupported "free"/"under $40" assertions never generated.

### P2-15 — Secure authenticated mobile editing

**Touch:** architecture/security design, Cloudflare integration, API, tests.  
**Implement only after a security review:** secure mobile shortlist/notes/approval.  
**Acceptance:** no unauthenticated write interface, no exposed localhost database service, auditable edits, predictable sync/conflict resolution, read-only fallback preserved.

---

## 16. Guidance for the coding agent — implementation process

1. **Inspect before editing:** Read `README.md`, `docs/agent-notes/README.md`, `status.md`, `decisions.md`, `gotchas.md`, and the relevant actual modules. Check branch, recent commits, and schema.
2. **Clarify what's already implemented:** Existing repo may have changed since this recommendation memo. Do not recreate working features or treat this file as authoritative current state.
3. **Turn the P0 backlog into small issues/PRs:** Schema/migrations first; then APIs; then UI; then analytics and presentation. Avoid a single massive refactor.
4. **Preserve behavior:** Maintain Ticketmaster/venue fetches, dedupe, baseline logic, setlist historical labeling, listening-data privacy, fact checker, deterministic onscreen text, Cloudflare protection, and scheduled operations.
5. **Test at every step:** Use existing mocked-network suite; add cases for score missingness, stale snapshots, edits, source failures, and temporal expiry. For UI, build/lint and test read-only snapshot compatibility.
6. **Never silently overwrite creator decisions:** Version facts and drafts; preserve approval notes, edits, dismissal, past posts, and metrics.
7. **Avoid risky infra changes:** Do not accidentally expose the local dashboard, leave Ollama runners in RAM, disrupt Homebridge, or lower Cloudflare Access safeguards.
8. **Security and privacy:** No keys or real private listening records in Git or prompts. Do not leak personal listening data via hosted snapshots. No need to collect viewer personal data.
9. **Document feature status:** Mark each proposal as planned, in progress, implemented, validated, or explicitly deferred. Update handoff notes and migration details.
10. **Seek user approval before merges or scope expansion:** Follow existing repository workflow and don't conflate this roadmap with permission to autonomously publish or change external accounts.

### Definition of done for a new creator feature

A feature is not "done" just because the screen renders. Require:

- A clear creator problem solved and a simple way to use it
- Source-backed data and truthful handling of unknowns
- No regression to event detection and script accuracy
- Tested edge cases and non-destructive data migrations
- Reasonable N95 performance and operational safety
- Understandable UI explanation and documentation
- No unauthorized posting and safe permissions
- A way to tell whether it improved speed, content quality, or audience outcomes

---

## 17. Risks, caveats, and choices worth remembering

### What was *observed* vs what is only *proposed*

**Observed in source/repository notes:** architecture, current detectors and thresholds, schedules, stack, prompt safeguards, local/static dashboards, the presence of existing test infrastructure.

**Not independently verified:** successful ongoing live runs on the user's mini PC, current source reliability beyond repository notes, the creator's TikTok analytics or account audience, exact video performance, and whether any quoted October/November opportunities are still correct at implementation time.

**Proposed, not validated:** the opportunity-score weights, the example dashboard figures, estimated viral potential, suggested content cadence, and the relative effectiveness of individual TikTok formats.

### Risks to actively manage

- **Trend staleness:** Do not freeze dated observations into evergreen recommendations.
- **Clickbait:** A surprising hook cannot imply fake scarcity, first-time status, sellouts, or fake statistics.
- **Small sample size:** One viral post doesn't prove a format or weighting causes higher performance.
- **Platform data access:** TikTok-related APIs and Creator Search Insights access may be restricted; never assume a supported analytics/search endpoint.
- **Copyright:** Music, album artwork, artist images, and videos are not automatically licensed for reuse.
- **Inaccurate prices:** Ticketmaster has incomplete price coverage; taxes/fees and availability change.
- **Venue/artist alias mismatches:** Namesakes, missing genres, and different seller calendars require careful entity resolution.
- **Source outages:** A scraper failing must not convert the whole database into cancelled/removed events.
- **Model hallucinations:** Preserve and extend deterministic facts and the existing tripwire.
- **Performance:** Ollama process lifecycle has previously caused memory issues. Model usage should be selective and observable.
- **Editor state loss:** Rebuilding a DB or replacing draft rows can destroy real human approval/review work.
- **Sensitive data:** Personal listening records should remain private and off public repos and unnecessary network interfaces.
- **Mobile security:** Static protected snapshots are not equivalent to a secured writable app.
- **Scope creep:** Content quality and creator outcomes should determine which extra event sources are worth maintaining.

---

## 18. Suggested next actions in order

### First agent work session

- [ ] Read current repository, agent notes, and this roadmap.
- [ ] Confirm current branch and any uncommitted changes; do not overwrite user work.
- [ ] Audit current schema and locate the minimal integration points for content opportunities and posted metrics.
- [ ] Write a short implementation plan for P0-01 through P0-04, including migrations, tests, API contracts, and UI states.
- [ ] Start with a narrow vertical slice: one existing alert becomes one creator-visible opportunity with clear ranking reasons; creator can mark it posted and enter metrics.
- [ ] Show the user the first working slice for approval before larger UI or data-source expansion.

### First creator testing session

- [ ] Choose a few live, **reverified** upcoming Philly stories.
- [ ] Test 2–3 hook styles across several suitable videos.
- [ ] Record source facts and creation/post times.
- [ ] Compare 24-hour and 7-day outcomes using age-aligned metrics.
- [ ] Record how much prep/production time the assistant actually saves.

### After evidence accumulates

- [ ] Tune the opportunity model.
- [ ] Improve concise genre-specific roundups.
- [ ] Add slideshow export with rights-aware asset handling.
- [ ] Add measured trend intake and possibly a low-cost midday check.
- [ ] Expand to food/festival content only if audience demand and source quality justify it.

---

## 19. References for continuation

### Repository sources

- https://github.com/bryanygan/event-tracker
- https://github.com/bryanygan/event-tracker/blob/main/README.md
- https://github.com/bryanygan/event-tracker/blob/main/event-tracker-pipeline.md
- https://github.com/bryanygan/event-tracker/tree/main/docs/agent-notes
- https://github.com/bryanygan/event-tracker/blob/main/tracker/scoring.py
- https://github.com/bryanygan/event-tracker/blob/main/tracker/detectors.py
- https://github.com/bryanygan/event-tracker/blob/main/tracker/llm.py
- https://github.com/bryanygan/event-tracker/blob/main/tracker/templates.py
- https://github.com/bryanygan/event-tracker/blob/main/tracker/db.py
- https://github.com/bryanygan/event-tracker/blob/main/tracker/api.py
- https://github.com/bryanygan/event-tracker/blob/main/dashboard/src/App.tsx

### Sources to re-check when researching live trends

- TikTok's official **What's Next** trend reports (edition/date-specific)
- TikTok **Creator Search Insights** documentation and actual in-app availability
- TikTok **Creative Center** trend/hashtag information (subject to location/access limitations)
- TikTok music and **Commercial Music Library** usage terms
- Official Philadelphia event/venue/promoter pages; Visit Philadelphia event calendars where useful
- Official ticketing/event listings and time zones
- Analytics from the creator's own TikTok account, if available and authorized

**Do not treat these source names as proof of a current trend. Search and record the actual dated evidence before presenting factual trend claims.**

---

## 20. One-sentence product vision

**Transform Philly Events Tracker from a reliable event watcher and script drafter into a local-first creator command center that discovers timely opportunities, explains why they matter, helps produce distinctive videos, and improves its recommendations using the creator's actual performance—without inventing facts or posting automatically.**
