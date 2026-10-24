// Typed wrappers around the Python API (tracker/api.py).

export type ShowFacts = {
  artist?: string
  event_name?: string
  date: string
  time?: string
  venue?: string
  genre?: string
  price?: string
  status?: string
  url?: string
  support?: string
}

export type Draft = {
  id: number
  alert_id: number
  format: string
  model: string
  hook: string
  script: string
  caption: string
  hashtags: string
  onscreen: string[]
  warnings: string[]
  facts_changes: string[] // what a later run changed in the alert since this draft was written
  angle: string | null // tracker/angles.py key the draft was written from
  created_at: string
  status: 'pending' | 'approved' | 'filmed' | 'rejected'
  notes: string | null
}

export type Alert = {
  id: number
  key: string
  kind: 'new_tour' | 'presale_soon' | 'weekend' | 'stacked_month' | 'status_change' | 'team_week' | 'big_game' | 'music_story' | 'new_music_friday' | 'upcoming_albums' | 'emerging_artists'
  title: string
  score: number
  facts: { shows?: ShowFacts[]; show?: ShowFacts; [k: string]: unknown }
  created_at: string
  updated_at: string | null // last time a run refreshed the facts
  status: 'new' | 'drafted' | 'dismissed'
  drafts: Draft[]
  posts: { id: number; draft_id: number | null; url: string | null; posted_at: string }[]
  angles: Angle[]
  context: WebContext | null // latest web lookup for this artist (tracker/context.py)
  context_supported: boolean // single-artist ideas only
  music_news?: MusicNewsItem[] // event ideas: the acts' recent music stories (M8); headlines to read, never facts
}

export type MusicNewsItem = {
  story_id: number; artist: string; title: string; type_label: string; release_status: string | null
  verification: 'unverified' | 'corroborated' | 'confirmed' | 'conflicting'; sensitive: boolean; last_seen: string
  url: string | null
}

// Web reading material from Perplexity: never facts, never used in drafts
export type WebContext = {
  id: number; answer: string; provider: string; created_at: string; fresh: boolean
  sources: { id: number; title: string | null; url: string; date: string | null }[]
}
// 9:16 slides (tracker/slides.py): words built from facts, your edits on top; drawn by slides/render.ts
export type SlideKind = 'title' | 'show' | 'sales' | 'cta'
export type SlideImage = { data_url: string; credit: string; rights: string }
export type Slide = {
  id: string; kind: SlideKind; fields: Record<string, string>
  hidden?: boolean; edited?: string[]; image?: SlideImage
  pick?: boolean // a roundup's 3-5 picks (tracker/slides.py)
}
export type SlideManifest = {
  version: number; format: string | null; theme: string; brand: string
  shows_total: number; shows_on_slides: number; slides: Slide[]
}
export type SlideOverride = Record<string, string | boolean | SlideImage | null>
export type SlideDeck = {
  alert_id: number; saved: boolean; base: SlideManifest; manifest: SlideManifest
  overrides: Record<string, SlideOverride>; facts_changes: string[]; updated_at: string | null
  rights: Record<string, string>; dropped?: string[]
}
export type ContextUsage ={ today: number; auto_today: number; cap: number; auto_cap: number; cost_today_usd: number; enabled: boolean }

// Ways to frame the video, built from the facts without the model (tracker/angles.py)
export type Angle = { key: string; label: string; hook: string; pitch: string; style: string; cta: string }

export const VIDEO_STYLES = ['voiceover', 'slideshow', 'footage', 'face-to-camera', 'other'] as const
export type VideoStyle = (typeof VIDEO_STYLES)[number]
export const COUNT_METRICS = ['views', 'likes', 'comments', 'shares', 'saves', 'followers_gained'] as const

export type Metrics = {
  id: number
  post_id: number
  observed_at: string
  age_hours: number
  views: number | null
  likes: number | null
  comments: number | null
  shares: number | null
  saves: number | null
  followers_gained: number | null
  avg_watch_seconds: number | null
  completion_rate: number | null
  notes: string | null
}
export type MetricsInput = Partial<Omit<Metrics, 'id' | 'post_id' | 'age_hours'>>

export type Checkpoint = '24h' | '7d'

export type Post = {
  id: number
  alert_id: number | null
  draft_id: number | null
  url: string | null
  title: string
  posted_at: string
  format: string | null
  video_style: VideoStyle | null
  hook: string | null
  length_seconds: number | null
  notes: string | null
  series: string | null
  age_hours: number
  metrics: Metrics[]
  checkpoints: Record<Checkpoint, number | null> // id of the snapshot used for each comparison age
  due: Checkpoint[]
}
export type PostInput = Partial<Pick<Post, 'alert_id' | 'draft_id' | 'url' | 'title' | 'posted_at' | 'format' |
  'video_style' | 'hook' | 'length_seconds' | 'notes' | 'series'>>

export type Analytics = {
  group_by: 'format' | 'video_style' | 'pillar' | 'music_format'
  min_posts: number
  checkpoints: Record<Checkpoint, { target_hours: number; window_hours: [number, number] }>
  groups: {
    group: string
    posts: number
    checkpoints: Record<Checkpoint, {
      n: number
      enough: boolean
      median_views: number | null
      per_1k_views: Record<'likes' | 'comments' | 'shares' | 'saves' | 'followers_gained', number | null>
    }>
  }[]
}

export type EventRow = {
  id: string
  name: string
  headliner: string
  support: string | null
  local_date: string
  local_time: string | null
  status: string | null
  venue_name: string | null
  genre: string | null
  subgenre: string | null
  // listing = the show's own listing; artist = Ticketmaster's genre for the headliner;
  // tm_search = venue-calendar act matched by name on Ticketmaster (inferred);
  // deezer = the artist's Deezer releases (fills a gap, or replaces a wrong Ticketmaster label). See tracker/genres.py.
  genre_source?: 'listing' | 'artist' | 'tm_search' | 'deezer' | null
  ticketmaster_genre?: string | null  // Ticketmaster's label when Deezer's replaced it
  price_min: number | null
  price_max: number | null
  onsale_start: string | null
  url: string | null
  score: number
  popularity: number | null
  fans: number | null
  my_plays: number
  niche: boolean
  first_seen: string
}

export type Stage = 'new' | 'shortlisted' | 'review' | 'needs_refresh' | 'approved' | 'filmed' | 'posted' |
  'measured' | 'snoozed' | 'expired' | 'dismissed'

export type QueueItem = {
  alert_id: number
  kind: Alert['kind']
  title: string
  notability: number
  created_at: string
  updated_at: string | null
  shortlisted: boolean
  snoozed_until: string | null
  note: string | null
  your_plays: number
  links: { label: string; url: string }[]
  draft: Pick<Draft, 'id' | 'status' | 'hook' | 'warnings' | 'facts_changes'> | null
  posts: { id: number; url: string | null; posted_at: string }[]
  score: number | null // experimental opportunity score, 0-100
  band: 'strong' | 'medium' | 'low' | null
  coverage: number // share of the score's weight that had data
  components: { key: string; label: string; weight: number; value: number | null; why: string }[]
  reasons: string[]
  caveats: string[]
  post_by: string | null
  expires_at: string | null
  stage: Stage
  rules_version: string
  trend_notes: TrendMatch[] // your trend notes that match this idea, expired ones included (marked)
  planned_for: string | null // when you plan to post it (UTC ISO); nothing posts by itself
  series: string | null // your series, else the format's default
  series_set: string | null // the series you typed (null = the default)
  format: string
  lane: 'lane' | 'other' | 'sports' | 'music'
  pillar: 'events' | 'music' // music: a music story you sent, or a music roundup (tracker/music_formats.py)
}

// Editorial calendar (tracker/planner.py)
export type CalendarItem = Pick<QueueItem, 'alert_id' | 'kind' | 'format' | 'title' | 'stage' | 'band' | 'score' |
  'post_by' | 'expires_at' | 'planned_for' | 'series' | 'series_set' | 'lane'> & {
  late: string | null // 'after it goes stale' / 'after its post-by time'
}
export type CalendarPost = { id: number; alert_id: number | null; title: string; posted_at: string; url: string | null;
  format: string | null; series: string | null; lane: QueueItem['lane'] | null }
export type CalendarDay = { date: string; label: string; past: boolean; today: boolean
  planned: CalendarItem[]; posted: CalendarPost[]; due: CalendarItem[] }
export type Calendar = {
  start: string
  days: CalendarDay[]
  mix: { week: string; label: string; formats: Record<string, number>; lane: number; music: number }[]
  series: { name: string; last_posted: string | null; next_planned: string | null; cadence_days: number | null
    behind: boolean; ideas: number }[]
  unplanned: CalendarItem[]
  default_hour: number
}

// Trends you noticed, typed in by hand (tracker/trends.py). Evidence, never facts for drafts.
export const TREND_KINDS = ['search', 'sound', 'hashtag', 'topic', 'other'] as const
export const TREND_SCOPES = ['philly', 'national', 'unknown'] as const
export const TREND_CONFIDENCE = ['low', 'medium', 'high'] as const
export type TrendNote = {
  id: number
  kind: (typeof TREND_KINDS)[number]
  text: string
  terms: string[]
  scope: (typeof TREND_SCOPES)[number]
  confidence: (typeof TREND_CONFIDENCE)[number]
  momentum: 'rising' | 'stable' | 'uncertain' | null
  source_url: string | null
  notes: string | null
  observed_at: string
  expires_at: string
  expired: boolean
}
export type TrendInput = Partial<Omit<TrendNote, 'id' | 'expired'>>
export type TrendMatch = Pick<TrendNote, 'id' | 'kind' | 'text' | 'scope' | 'confidence' | 'observed_at' | 'expires_at' | 'expired'> & {
  matched_term: string; match: 'exact' | 'title'; value: number
}

export type Queue = {
  items: QueueItem[]
  counts: { new: number; time_sensitive: number; to_review: number; ready_to_film: number; needs_refresh: number }
  weights: Record<string, number>
  rules_version: string
}

export type AlertPatch = Partial<{ status: Alert['status']; shortlisted: boolean; snoozed_until: string | null; note: string | null
  planned_for: string | null; series: string | null }>

export type Run = {
  id: number
  started_at: string
  finished_at: string | null
  events_fetched: number
  api_calls: number
  ok: number
  error: string | null
}

// Writes made from your phone through the Cloudflare Tunnel (tracker/remote.py)
export type RemoteEdits = {
  enabled: boolean
  host: string | null
  edits: { id: number; at: string; email: string; method: string; path: string; status: number }[]
}

export type Summary = {
  upcoming_events: number
  open_alerts: number
  pending_drafts: number
  last_run: Run | null
  model: string
  has_tm_key: boolean
  generated_at?: string // static snapshots only
  edit_url?: string | null // static snapshots only: the editable dashboard through the tunnel, if set up
  publish?: { at: string; ok: boolean; message: string; url?: string } | null
  source_problems?: { source: string; error: string }[]
  posts_this_week: number
  metrics_due: number
  // Claude drafting: calls today against the daily cap, and this month's spend (enabled=false: Ollama only)
  drafting?: { enabled: boolean; model: string; today: number; cap: number; month_calls: number; month_cost_usd: number }
}

export type Job = {
  name: string | null; started: string | null; finished: string | null; error: string | null; running: boolean
  // draft jobs: the alert, the current step, and about how many seconds the whole thing takes on this PC
  alert_id?: number | null; step?: string | null; estimate?: number | null; elapsed?: number | null
}

// Music news (tracker/news.py, roadmap M1): feed articles and source health. Not in the read-only snapshot yet.
export type NewsArticle = {
  id: number; url: string | null; title: string; excerpt: string | null
  published_at: string | null; fetched_at: string; seen_at: string; categories: string[]
  first_read: boolean // already in the feed the first time it was read (its time may be "when we started")
  source_key: string; source_name: string
  artists: NewsArtistTag[] // watchlist artists it mentions (tracker/watchlist.py), most played first
}
export type NewsArtistTag = {
  id: number; name: string; tier: WatchTier; plays: number
  match: 'exact' | 'alias' | 'owner'; location: 'title' | 'excerpt'; confidence: 'high' | 'medium' | 'low'
}
export type WatchTier = 'core' | 'adjacent' | 'watch' | 'breakout' | 'dropped'
export type WatchArtist = {
  id: number; name: string; tier: WatchTier; origin: 'listening' | 'manual' | 'related'; aliases: string[]; muted: boolean
  user_edited: boolean; common_word: boolean; common_word_auto: boolean; common_word_override: number | null
  ambiguous_name: boolean; mbid: string | null; deezer_id: number | null; attraction_id: string | null
  plays: number; articles_30d: number; bucket: string | null; seen_live: boolean
}
// Discover (tracker/discovery.py, roadmap M7): candidates with their evidence; momentum is experimental
export type MomentumSignal = {
  provider: string; metric: string; label: string; status: 'collecting' | 'too_small' | 'ready'; ready: boolean
  growing: boolean; counts: boolean; observations: number; latest?: number; weekly_growth?: number; why: string
}
export type Momentum = { ready: boolean; signals: MomentumSignal[]; growing: number; two_signals: boolean; value: number | null; summary: string }
export type Candidate = {
  id: number; name: string; status: 'new' | 'accepted' | 'dismissed'; score: number; why: string[]; tags: string[]
  lastfm_url: string | null; similar_to: string[]; metrics: Record<string, number>; momentum: Momentum | null
  first_seen: string; breakout: boolean
}
export type DiscoverList = {
  candidates: Candidate[]; refreshed_at: string | null; enabled: boolean
  rules: { min_observations: number; min_days: number; growth_per_week: number; min_mentions: number }
}
export type DiscoverAction = 'watch' | 'adjacent' | 'dismiss' | 'restore'
export type StoryArtist = { id: number; name: string; tier: WatchTier; plays: number; seen_live: boolean }
export type StoryFeedback = 'interested' | 'not_my_lane' | 'already_covered'
export type Story = {
  id: number; key: string; story_type: string; rule: string | null; title: string
  primary_artist: StoryArtist | null; other_artists: StoryArtist[]
  release_title: string | null; release_status: 'rumored' | 'teased' | 'announced' | 'scheduled' | 'released' | null
  release_date: string | null; verification: 'unverified' | 'corroborated' | 'confirmed' | 'conflicting'
  sensitive: boolean; dated: boolean; origin: 'news' | 'catalog'; first_seen: string; last_seen: string
  feedback: StoryFeedback | null; alert_id: number | null
  sources: { id: number; title: string; url: string | null; source_name: string; seen_at: string }[]
  source_count: number
  evidence: { kind: string; provider: string; url: string | null; claim: string; value: string; excerpt: string | null; observed_at: string }[]
  // Music score (tracker/music.py, M5): the queue's layout, with its own components
  score: number | null; band: QueueItem['band']; coverage: number; components: QueueItem['components']
  reasons: string[]; caveats: string[]; angle: string | null; rules_version: string
  philly_shows: { date: string; venue: string | null; url: string | null; headliner: boolean }[]
  trend_notes: TrendMatch[]
}
export type WatchPatch = Partial<{ tier: WatchTier; muted: boolean; aliases: string[]; common_word_override: boolean | null; seen_live: boolean }>
export type NewsSource = {
  key: string; name: string; url: string; enabled: boolean; niches: string[]; poll_minutes: number
  kind: 'rss' | 'youtube'; origin: 'config' | 'musicbrainz'
  official: boolean // an artist's own channel (M8)
  needs_key: boolean // YouTube without YOUTUBE_API_KEY: not read
  last_fetched_at: string | null; last_ok_at: string | null; last_not_modified_at: string | null
  last_error: string | null; failures: number; terms_note: string | null; articles: number; last_24h: number
}
export type NewsHealth = {
  sources: NewsSource[]; checks: Run[]
  youtube?: { enabled: boolean; units_today: number; daily_cap: number }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json()
}

// Static mode (`npm run build:static`): the Cloudflare Pages snapshot. No server; data comes from
// JSON files the PC exports after each run (tracker/publish.py), and nothing can be changed.
export const STATIC = import.meta.env.VITE_STATIC === '1'

const snapshots: Record<string, Promise<unknown>> = {}
function snapshot<T>(name: string): Promise<T> {
  snapshots[name] ??= fetch(`data/${name}.json`, { cache: 'no-store' }).then((r) => {
    if (!r.ok) throw new Error(`Snapshot file ${name}.json missing (${r.status})`)
    return r.json()
  })
  return snapshots[name] as Promise<T>
}
const readOnly = () => Promise.reject(new Error('Read-only snapshot: make changes on the local dashboard'))

const staticApi = {
  summary: () => snapshot<Summary>('summary'),
  job: () => Promise.resolve<Job>({ name: null, started: null, finished: null, error: null, running: false }),
  alerts: (status: 'open' | 'dismissed' | 'all') =>
    snapshot<Alert[]>('alerts').then((all) => (status === 'dismissed' ? [] : all)),
  setAlertStatus: readOnly,
  regenerate: readOnly,
  lookupContext: readOnly,
  slides: (id: number) => snapshot<Record<string, SlideDeck>>('slides').then((all) => {
    if (!all[id]) throw new Error('No slides for this idea in the snapshot')
    return all[id]
  }),
  saveSlides: readOnly,
  rebuildSlides: readOnly,
  resetSlides: readOnly,
  updateDraft: readOnly,
  events: ({ q = '', genre = '', days = 60 }: { q?: string; genre?: string; days?: number }) =>
    snapshot<EventRow[]>('events').then((all) => {
      const end = new Date(Date.now() + days * 864e5).toISOString().slice(0, 10)
      const ql = q.toLowerCase()
      return all.filter((e) => e.local_date <= end && (!genre || e.genre === genre) &&
        (!ql || [e.name, e.headliner, e.venue_name].some((s) => s?.toLowerCase().includes(ql))))
    }),
  genres: () => snapshot<string[]>('genres'),
  runs: () => snapshot<Run[]>('runs'),
  remoteEdits: () => Promise.resolve<RemoteEdits>({ enabled: false, host: null, edits: [] }),
  triggerRun: readOnly,
  posts: () => snapshot<Post[]>('posts'),
  queue: () => snapshot<Queue>('queue'),
  calendar: (_start?: string) => snapshot<Calendar>('calendar'),
  updateAlert: readOnly,
  analytics: (groupBy: Analytics['group_by']) =>
    snapshot<Record<string, Analytics>>('analytics').then((all) => all[groupBy]),
  createPost: readOnly,
  updatePost: readOnly,
  deletePost: readOnly,
  addMetrics: readOnly,
  deleteMetrics: readOnly,
  trends: () => snapshot<TrendNote[]>('trends').catch(() => [] as TrendNote[]), // older snapshots have no file
  createTrend: readOnly,
  updateTrend: readOnly,
  deleteTrend: readOnly,
  news: (_days: number, _mine = false) => Promise.resolve<NewsArticle[]>([]),
  newsSources: () => Promise.resolve<NewsHealth>({ sources: [], checks: [] }),
  watchlist: () => Promise.resolve<WatchArtist[]>([]),
  stories: (_days: number, _origin?: Story['origin'], _sort?: 'score' | 'recent') => Promise.resolve<Story[]>([]),
  storyFeedback: readOnly,
  sendStory: readOnly,
  discover: (_status: Candidate['status'] = 'new') =>
    Promise.resolve<DiscoverList>({ candidates: [], refreshed_at: null, enabled: false,
      rules: { min_observations: 3, min_days: 7, growth_per_week: 0.03, min_mentions: 2 } }),
  decideCandidate: readOnly,
  candidatesIdea: readOnly,
  addWatch: readOnly,
  updateWatch: readOnly,
}

const liveApi = {
  summary: () => req<Summary>('/api/summary'),
  job: () => req<Job>('/api/job'),
  alerts: (status: 'open' | 'dismissed' | 'all') => req<Alert[]>(`/api/alerts?status=${status}`),
  setAlertStatus: (id: number, status: Alert['status']) =>
    req(`/api/alerts/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  regenerate: (id: number, angle?: string) =>
    req<Job>(`/api/alerts/${id}/draft${angle ? `?angle=${encodeURIComponent(angle)}` : ''}`, { method: 'POST' }),
  slides: (id: number) => req<SlideDeck>(`/api/alerts/${id}/slides`),
  saveSlides: (id: number, overrides: SlideDeck['overrides']) =>
    req<SlideDeck>(`/api/alerts/${id}/slides`, { method: 'PUT', body: JSON.stringify({ overrides }) }),
  rebuildSlides: (id: number) => req<SlideDeck>(`/api/alerts/${id}/slides/rebuild`, { method: 'POST' }),
  resetSlides: (id: number) => req<SlideDeck>(`/api/alerts/${id}/slides`, { method: 'DELETE' }),
  lookupContext: (id: number, refresh = false) =>
    req<{ context: WebContext; usage: ContextUsage }>(`/api/alerts/${id}/context${refresh ? '?refresh=true' : ''}`, { method: 'POST' }),
  updateDraft: (id: number, fields: Partial<Pick<Draft, 'status' | 'notes' | 'hook' | 'script' | 'caption' | 'hashtags'>>) =>
    req(`/api/drafts/${id}`, { method: 'PATCH', body: JSON.stringify(fields) }),
  events: (params: { q?: string; genre?: string; days?: number }) =>
    req<EventRow[]>('/api/events?' + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v ?? '')]))),
  genres: () => req<string[]>('/api/genres'),
  runs: () => req<Run[]>('/api/runs'),
  remoteEdits: () => req<RemoteEdits>('/api/remote-edits'),
  triggerRun: () => req<Job>('/api/run', { method: 'POST' }),
  publish: () => req<Job>('/api/publish', { method: 'POST' }),
  posts: () => req<Post[]>('/api/posts'),
  queue: () => req<Queue>('/api/queue'),
  calendar: (start?: string) => req<Calendar>(`/api/calendar${start ? `?start=${start}` : ''}`),
  updateAlert: (id: number, fields: AlertPatch) =>
    req(`/api/alerts/${id}`, { method: 'PATCH', body: JSON.stringify(fields) }),
  analytics: (groupBy: Analytics['group_by']) => req<Analytics>(`/api/analytics?group_by=${groupBy}`),
  createPost: (fields: PostInput) => req<{ id: number }>('/api/posts', { method: 'POST', body: JSON.stringify(fields) }),
  updatePost: (id: number, fields: PostInput) =>
    req(`/api/posts/${id}`, { method: 'PATCH', body: JSON.stringify(fields) }),
  deletePost: (id: number) => req(`/api/posts/${id}`, { method: 'DELETE' }),
  addMetrics: (postId: number, fields: MetricsInput) =>
    req<{ id: number }>(`/api/posts/${postId}/metrics`, { method: 'POST', body: JSON.stringify(fields) }),
  deleteMetrics: (id: number) => req(`/api/metrics/${id}`, { method: 'DELETE' }),
  trends: () => req<TrendNote[]>('/api/trends'),
  createTrend: (fields: TrendInput) => req<{ id: number }>('/api/trends', { method: 'POST', body: JSON.stringify(fields) }),
  updateTrend: (id: number, fields: TrendInput) =>
    req(`/api/trends/${id}`, { method: 'PATCH', body: JSON.stringify(fields) }),
  deleteTrend: (id: number) => req(`/api/trends/${id}`, { method: 'DELETE' }),
  news: (days: number, mine = false) => req<NewsArticle[]>(`/api/news?days=${days}${mine ? '&mine=true' : ''}`),
  newsSources: () => req<NewsHealth>('/api/news/sources'),
  watchlist: () => req<WatchArtist[]>('/api/watchlist'),
  stories: (days: number, origin?: Story['origin'], sort: 'score' | 'recent' = 'score') =>
    req<Story[]>(`/api/news/stories?days=${days}&sort=${sort}${origin ? `&origin=${origin}` : ''}`),
  storyFeedback: (id: number, feedback: StoryFeedback | null) =>
    req<{ ok: boolean }>(`/api/news/stories/${id}/feedback`, { method: 'POST', body: JSON.stringify({ feedback }) }),
  sendStory: (id: number) => req<{ alert_id: number }>(`/api/news/stories/${id}/send`, { method: 'POST' }),
  discover: (status: Candidate['status'] = 'new') => req<DiscoverList>(`/api/discover?status=${status}`),
  decideCandidate: (id: number, action: DiscoverAction) =>
    req<{ id: number; status: string }>(`/api/discover/${id}/decide`, { method: 'POST', body: JSON.stringify({ action }) }),
  candidatesIdea: (ids: number[]) =>
    req<{ alert_id: number }>('/api/discover/idea', { method: 'POST', body: JSON.stringify({ ids }) }),
  addWatch: (name: string, tier: WatchTier, aliases: string[] = []) =>
    req<{ id: number }>('/api/watchlist', { method: 'POST', body: JSON.stringify({ name, tier, aliases }) }),
  updateWatch: (id: number, fields: WatchPatch) =>
    req<WatchArtist>(`/api/watchlist/${id}`, { method: 'PATCH', body: JSON.stringify(fields) }),
}

export const api: typeof liveApi = STATIC ? { ...staticApi, publish: readOnly } : liveApi
