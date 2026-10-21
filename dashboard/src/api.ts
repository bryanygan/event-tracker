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
  created_at: string
  status: 'pending' | 'approved' | 'filmed' | 'rejected'
  notes: string | null
}

export type Alert = {
  id: number
  key: string
  kind: 'new_tour' | 'presale_soon' | 'weekend' | 'stacked_month' | 'status_change'
  title: string
  score: number
  facts: { shows?: ShowFacts[]; show?: ShowFacts; [k: string]: unknown }
  created_at: string
  status: 'new' | 'drafted' | 'dismissed'
  drafts: Draft[]
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

export const api = {
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
}
