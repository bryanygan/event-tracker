import { useEffect, useMemo, useState } from 'react'
import { api, type NewsArticle, type NewsHealth, type NewsSource } from '../api'
import { fmtWhen } from '../App'

// Music news, step 1 (roadmap M1): what the feeds published, read every 30 minutes. Matching your artists,
// grouping into stories and scoring come later (M2-M5); for now this is the raw feed plus source health.
export default function NewsView({ refreshKey }: { refreshKey: number }) {
  const [days, setDays] = useState(2)
  const [q, setQ] = useState('')
  const [source, setSource] = useState('')
  const [bySource, setBySource] = useState(false)
  const [articles, setArticles] = useState<NewsArticle[] | null>(null)
  const [health, setHealth] = useState<NewsHealth | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.news(days).then(setArticles).catch((e) => setError(String(e)))
    api.newsSources().then(setHealth).catch((e) => setError(String(e)))
  }, [days, refreshKey])

  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return (articles ?? []).filter((a) => (!source || a.source_key === source) &&
      (!ql || a.title.toLowerCase().includes(ql) || (a.excerpt ?? '').toLowerCase().includes(ql)))
  }, [articles, q, source])

  const groups = useMemo(() => {
    const m = new Map<string, NewsArticle[]>()
    for (const a of shown) m.set(a.source_name, [...(m.get(a.source_name) ?? []), a])
    return [...m.entries()].sort((x, y) => y[1].length - x[1].length)
  }, [shown])

  const sources = health?.sources.filter((s) => s.enabled) ?? []
  const broken = sources.filter((s) => s.last_error)
  const lastCheck = health?.checks[0]
  return (
    <>
      <p className="muted small">
        Headlines from {sources.length} music-news feeds, checked every 30 minutes
        {lastCheck ? ` (last check ${fmtWhen(lastCheck.started_at)}, ${lastCheck.events_fetched} new)` : ''}.
        Matching your artists and grouping duplicate stories come next; nothing here becomes an idea by itself.
      </p>
      {error && <div className="banner error">{error}</div>}
      {broken.length > 0 && (
        <div className="banner">
          Feed problem: {broken.map((s) => `${s.name} (${s.last_error})`).join(' · ')}. Their articles are kept; it's
          retried with a longer wait.
        </div>
      )}

      <div className="toolbar">
        <input placeholder="Search headlines" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">All sources</option>
          {sources.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
        </select>
        <select value={days} onChange={(e) => setDays(+e.target.value)}>
          {[1, 2, 3, 7, 14].map((d) => <option key={d} value={d}>{d === 1 ? 'Last 24 hours' : `Last ${d} days`}</option>)}
        </select>
        <label className="small"><input type="checkbox" checked={bySource} onChange={(e) => setBySource(e.target.checked)} /> Group by source</label>
        <span className="muted small">{articles ? `${shown.length} articles` : 'Loading…'}</span>
      </div>

      {articles && shown.length === 0 && <p className="muted">No articles in this window yet.</p>}
      {bySource
        ? groups.map(([name, list]) => (
            <section key={name}>
              <h3 className="section-title">{name} <span className="muted small">{list.length}</span></h3>
              <ArticleTable articles={list} showSource={false} />
            </section>
          ))
        : shown.length > 0 && <ArticleTable articles={shown} showSource />}

      <h3>Sources</h3>
      <SourcesTable sources={health?.sources ?? []} />
    </>
  )
}

function ArticleTable({ articles, showSource }: { articles: NewsArticle[]; showSource: boolean }) {
  return (
    <div className="table-wrap">
      <table className="events">
        <thead><tr><th>When</th>{showSource && <th>Source</th>}<th>Headline</th></tr></thead>
        <tbody>
          {articles.map((a) => (
            <tr key={a.id}>
              <td className="nowrap" title={a.published_at ? 'published (from the feed)' : 'first seen (the feed gives no date)'}>
                {a.published_at ? fmtWhen(a.published_at)
                  : a.first_read ? <span className="muted">before tracking</span>
                  : <>{fmtWhen(a.fetched_at)}<span className="muted"> ≈</span></>}
              </td>
              {showSource && <td className="nowrap">{a.source_name}</td>}
              <td>
                {a.url ? <a href={a.url} target="_blank" rel="noreferrer">{a.title}</a> : a.title}
                {a.excerpt && <div className="muted small">{a.excerpt}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SourcesTable({ sources }: { sources: NewsSource[] }) {
  return (
    <div className="table-wrap">
      <table className="events">
        <thead><tr><th>Source</th><th>Niches</th><th>Articles</th><th>Last 24h</th><th>Last OK</th><th>Last “nothing new”</th><th>Status</th></tr></thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.key} className={s.enabled ? '' : 'muted'}>
              <td><a href={s.url} target="_blank" rel="noreferrer" title={s.terms_note ?? ''}>{s.name}</a></td>
              <td className="small">{s.niches.join(', ')}</td>
              <td>{s.articles}</td>
              <td>{s.last_24h}</td>
              <td className="nowrap">{s.last_ok_at ? fmtWhen(s.last_ok_at) : '–'}</td>
              <td className="nowrap">{s.last_not_modified_at ? fmtWhen(s.last_not_modified_at) : '–'}</td>
              <td>
                {!s.enabled ? 'removed from config'
                  : s.last_error ? <span className="error-text">{s.last_error} ({s.failures}× in a row)</span>
                  : s.last_fetched_at ? 'ok' : 'not read yet'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        “Nothing new” means the site answered 304 to a conditional request: no download, no work. A failing feed is
        retried after 30, 60, then 120 minutes and never loses its stored articles.
      </p>
    </div>
  )
}
