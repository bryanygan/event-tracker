import { useCallback, useEffect, useState } from 'react'
import { api, STATIC, type Job, type Summary } from './api'
import AlertsView from './views/AlertsView'
import CalendarView from './views/CalendarView'
import EventsView from './views/EventsView'
import NewsView from './views/NewsView'
import PostsView from './views/PostsView'
import QueueView from './views/QueueView'
import RunsView from './views/RunsView'

type Tab = 'queue' | 'calendar' | 'alerts' | 'posts' | 'events' | 'runs' | 'news'
// Calendar and Music news are separate nice-to-have tabs, last (music news merges into What to post later, roadmap M9)
const TABS: Tab[] = ['queue', 'alerts', 'posts', 'events', 'runs', 'calendar', 'news']

export default function App() {
  // The tab lives in the URL hash (#alerts), so a tab can be bookmarked on the phone
  const [tab, setTabState] = useState<Tab>(() => TABS.find((t) => `#${t}` === window.location.hash || window.location.hash.startsWith(`#${t}/`)) ?? 'queue')
  const setTab = (t: Tab) => { setTabState(t); history.replaceState(null, '', t === 'queue' ? location.pathname + location.search : `#${t}`) }
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
    if (STATIC) return // nothing runs on a snapshot
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

  const publishNow = async () => {
    setError(null)
    try {
      setJob(await api.publish())
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
          <Stat label="posted this week" value={summary?.posts_this_week} />
        </div>
        {STATIC ? (
          <div className="run-box">
            <span className="pill">Read-only snapshot</span>
            <span className="muted small">
              {summary?.generated_at ? `Updated ${fmtWhen(summary.generated_at)}` : ''}
            </span>
          </div>
        ) : (
          <div className="run-box">
            <div className="actions-inline">
              <button onClick={runNow} disabled={job?.running || !summary?.has_tm_key}
                      title={summary?.has_tm_key ? '' : 'Add TICKETMASTER_CONSUMER_KEY to .env'}>
                {job?.running ? 'Working…' : 'Run pipeline now'}
              </button>
              {summary?.publish != null && (  // null = Pages snapshot turned off
                <button className="ghost" onClick={publishNow} disabled={job?.running}
                        title="Push a read-only snapshot to Cloudflare Pages">
                  Publish snapshot
                </button>
              )}
            </div>
            <span className="muted small">
              {job?.running
                ? `${job.name} since ${job.started?.slice(11)}`
                : last
                  ? `Last run ${fmtWhen(last.started_at)} · ${last.ok ? 'ok' : 'FAILED'}`
                  : 'No runs yet'}
            </span>
            {summary?.publish && (
              <span className={`small ${summary.publish.ok ? 'muted' : 'error-text'}`}>
                Web snapshot {fmtWhen(summary.publish.at)}: {summary.publish.message}
              </span>
            )}
          </div>
        )}
      </header>

      {STATIC && (
        <div className="banner">
          {summary?.edit_url
            ? <>Read-only copy, updated after each run. To make changes, open the <a href={summary.edit_url}>live
              dashboard</a>.</>
            : <>Read-only copy of the dashboard. Copy buttons work; edits and draft status changes happen on the
              mini PC at 127.0.0.1:8765 and show up here after the next publish.</>}
        </div>
      )}
      {!STATIC && summary && !summary.has_tm_key && (
        <div className="banner">TICKETMASTER_CONSUMER_KEY is missing from <code>.env</code>, so the pipeline can't fetch.</div>
      )}
      {job?.error && !job.running && <div className="banner error">Last job failed: {job.error}</div>}
      {!!summary?.source_problems?.length && (
        <div className="banner">
          Venue calendar problem: {summary.source_problems.map((p) => `${p.source} (${p.error})`).join(' · ')}.
          Their existing shows are kept; the site may have changed.
        </div>
      )}
      {error && <div className="banner error">{error}</div>}

      <nav>
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'tab active' : 'tab'} onClick={() => setTab(t)}>
            {{ queue: 'What to post', calendar: 'Calendar', alerts: 'Alerts & drafts', posts: 'Posted', events: 'Events', runs: 'Runs', news: 'Music news' }[t]}
            {t === 'posts' && !!summary?.metrics_due && <span className="badge" title="posts waiting for 24h/7d numbers">{summary.metrics_due}</span>}
          </button>
        ))}
      </nav>

      <main>
        {tab === 'queue' && <QueueView refreshKey={refreshKey} busy={!!job?.running} job={job} summary={summary} onJob={setJob} onChange={refresh} />}
        {tab === 'calendar' && <CalendarView refreshKey={refreshKey} onChange={refresh} />}
        {tab === 'alerts' && <AlertsView refreshKey={refreshKey} busy={!!job?.running} job={job} onJob={setJob} onChange={refresh} />}
        {tab === 'posts' && <PostsView refreshKey={refreshKey} onChange={refresh} />}
        {tab === 'events' && <EventsView refreshKey={refreshKey} />}
        {tab === 'runs' && <RunsView refreshKey={refreshKey} summary={summary} />}
        {tab === 'news' && <NewsView refreshKey={refreshKey} />}
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
