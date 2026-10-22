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
  kind: 'new_tour' | 'presale_soon' | 'weekend' | 'stacked_month' | 'status_change' | 'team_week' | 'big_game'
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
  age_hours: number
  metrics: Metrics[]
  checkpoints: Record<Checkpoint, number | null> // id of the snapshot used for each comparison age
  due: Checkpoint[]
}
export type PostInput = Partial<Pick<Post, 'alert_id' | 'draft_id' | 'url' | 'title' | 'posted_at' | 'format' |
  'video_style' | 'hook' | 'length_seconds' | 'notes'>>

export type Analytics = {
  group_by: 'format' | 'video_style'
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
  // tm_search = venue-calendar act matched by name on Ticketmaster (inferred). See tracker/genres.py.
  genre_source?: 'listing' | 'artist' | 'tm_search' | null
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

export type AlertPatch = Partial<{ status: Alert['status']; shortlisted: boolean; snoozed_until: string | null; note: string | null }>

export type Run = {
  id: number
  started_at: string
  finished_at: string | null
  events_fetched: number
  api_calls: number
  ok: number
  error: string | null
}

export type Summary = {
  upcoming_events: number
  open_alerts: number
  pending_drafts: number
  last_run: Run | null
  model: string
  has_tm_key: boolean
  generated_at?: string // static snapshots only
  publish?: { at: string; ok: boolean; message: string; url?: string } | null
  source_problems?: { source: string; error: string }[]
  posts_this_week: number
  metrics_due: number
}

export type Job = { name: string | null; started: string | null; finished: string | null; error: string | null; running: boolean }

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
  triggerRun: readOnly,
  posts: () => snapshot<Post[]>('posts'),
  queue: () => snapshot<Queue>('queue'),
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
  triggerRun: () => req<Job>('/api/run', { method: 'POST' }),
  publish: () => req<Job>('/api/publish', { method: 'POST' }),
  posts: () => req<Post[]>('/api/posts'),
  queue: () => req<Queue>('/api/queue'),
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
}

export const api: typeof liveApi = STATIC ? { ...staticApi, publish: readOnly } : liveApi
