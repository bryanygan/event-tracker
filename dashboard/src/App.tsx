import { useCallback, useEffect, useState } from 'react'
import { api, type Job, type Summary } from './api'
import AlertsView from './views/AlertsView'
import EventsView from './views/EventsView'
import RunsView from './views/RunsView'

type Tab = 'alerts' | 'events' | 'runs'

export default function App() {
  const [tab, setTab] = useState<Tab>('alerts')
  const [summary, setSummary] = useState<Summary | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [refreshKey, setRefreshKey] = useState(0) // bump to make views reload
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    api.summary().then(setSummary).catch((e) => setError(String(e)))
    setRefreshKey((k) => k + 1)
  }, [])

  useEffect(() => { api.summary().then(setSummary).catch((e) => setError(String(e))) }, [])

  // Poll the background job while one is running; refresh everything when it finishes.
  useEffect(() => {
    let wasRunning = false
    const tick = () =>
      api.job().then((j) => {
        setJob(j)
        if (wasRunning && !j.running) refresh()
        wasRunning = j.running
      }).catch(() => {})
    tick()
    const id = setInterval(tick, 3000)
    return () => clearInterval(id)
  }, [refresh])

  const runNow = async () => {
    setError(null)
    try {
      setJob(await api.triggerRun())
    } catch (e) {
      setError(String(e))
    }
  }

  const last = summary?.last_run
  return (
    <div className="app">
      <header>
        <div className="brand">
          <h1>Philly Events</h1>
          <span className="muted">content assistant · nothing posts automatically</span>
        </div>
        <div className="stats">
          <Stat label="upcoming events" value={summary?.upcoming_events} />
          <Stat label="open alerts" value={summary?.open_alerts} />
          <Stat label="drafts to review" value={summary?.pending_drafts} />
        </div>
        <div className="run-box">
          <button onClick={runNow} disabled={job?.running || !summary?.has_tm_key}
                  title={summary?.has_tm_key ? '' : 'Add TICKETMASTER_API_KEY to .env'}>
            {job?.running ? 'Working…' : 'Run pipeline now'}
          </button>
          <span className="muted small">
            {job?.running
              ? `${job.name} since ${job.started?.slice(11)}`
              : last
                ? `Last run ${fmtWhen(last.started_at)} · ${last.ok ? 'ok' : 'FAILED'}`
                : 'No runs yet'}
          </span>
        </div>
      </header>

      {summary && !summary.has_tm_key && (
        <div className="banner">TICKETMASTER_API_KEY is missing from <code>.env</code>, so the pipeline can't fetch.</div>
      )}
      {job?.error && !job.running && <div className="banner error">Last job failed: {job.error}</div>}
      {error && <div className="banner error">{error}</div>}

      <nav>
        {(['alerts', 'events', 'runs'] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? 'tab active' : 'tab'} onClick={() => setTab(t)}>
            {{ alerts: 'Alerts & drafts', events: 'Events', runs: 'Runs' }[t]}
          </button>
        ))}
      </nav>

      <main>
        {tab === 'alerts' && <AlertsView refreshKey={refreshKey} busy={!!job?.running} onJob={setJob} onChange={refresh} />}
        {tab === 'events' && <EventsView refreshKey={refreshKey} />}
        {tab === 'runs' && <RunsView refreshKey={refreshKey} />}
      </main>
    </div>
  )
}

function Stat({ label, value }: { label: string; value?: number }) {
  return (
    <div className="stat">
      <strong>{value ?? '–'}</strong>
      <span>{label}</span>
    </div>
  )
}

export function fmtWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
