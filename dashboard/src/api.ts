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
}

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
  analytics: (groupBy: Analytics['group_by']) =>
    snapshot<Record<string, Analytics>>('analytics').then((all) => all[groupBy]),
  createPost: readOnly,
  updatePost: readOnly,
  deletePost: readOnly,
  addMetrics: readOnly,
  deleteMetrics: readOnly,
}

const liveApi = {
  summary: () => req<Summary>('/api/summary'),
  job: () => req<Job>('/api/job'),
  alerts: (status: 'open' | 'dismissed' | 'all') => req<Alert[]>(`/api/alerts?status=${status}`),
  setAlertStatus: (id: number, status: Alert['status']) =>
    req(`/api/alerts/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  regenerate: (id: number) => req<Job>(`/api/alerts/${id}/draft`, { method: 'POST' }),
  updateDraft: (id: number, fields: Partial<Pick<Draft, 'status' | 'notes' | 'hook' | 'script' | 'caption' | 'hashtags'>>) =>
    req(`/api/drafts/${id}`, { method: 'PATCH', body: JSON.stringify(fields) }),
  events: (params: { q?: string; genre?: string; days?: number }) =>
    req<EventRow[]>('/api/events?' + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v ?? '')]))),
  genres: () => req<string[]>('/api/genres'),
  runs: () => req<Run[]>('/api/runs'),
  triggerRun: () => req<Job>('/api/run', { method: 'POST' }),
  publish: () => req<Job>('/api/publish', { method: 'POST' }),
  posts: () => req<Post[]>('/api/posts'),
  analytics: (groupBy: Analytics['group_by']) => req<Analytics>(`/api/analytics?group_by=${groupBy}`),
  createPost: (fields: PostInput) => req<{ id: number }>('/api/posts', { method: 'POST', body: JSON.stringify(fields) }),
  updatePost: (id: number, fields: PostInput) =>
    req(`/api/posts/${id}`, { method: 'PATCH', body: JSON.stringify(fields) }),
  deletePost: (id: number) => req(`/api/posts/${id}`, { method: 'DELETE' }),
  addMetrics: (postId: number, fields: MetricsInput) =>
    req<{ id: number }>(`/api/posts/${postId}/metrics`, { method: 'POST', body: JSON.stringify(fields) }),
  deleteMetrics: (id: number) => req(`/api/metrics/${id}`, { method: 'DELETE' }),
}

export const api: typeof liveApi = STATIC ? { ...staticApi, publish: readOnly } : liveApi
