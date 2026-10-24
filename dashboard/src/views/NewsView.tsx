import { useEffect, useMemo, useState } from 'react'
import { api, type NewsArticle, type NewsArtistTag, type NewsHealth, type NewsSource } from '../api'
import DiscoverView from './DiscoverView'
import StoriesList from './StoriesList'
import WatchlistPanel from './WatchlistPanel'
import { fmtWhen } from '../App'

// Music news (roadmap M1-M5): what the feeds published, read every 30 minutes, tagged with your watchlist artists,
// grouped into stories (M3), checked against the release catalogs (M4) and scored for you (M5); Discover (M7).
type NewsSub = 'stories' | 'releases' | 'discover' | 'articles'
const SUBS: NewsSub[] = ['stories', 'releases', 'discover', 'articles']

export default function NewsView({ refreshKey }: { refreshKey: number }) {
  // The sub-view rides in the URL hash too (#news/discover), so it can be bookmarked
  const [view, setViewState] = useState<NewsSub>(() => SUBS.find((s) => window.location.hash === `#news/${s}`) ?? 'stories')
  const setView = (v: NewsSub) => { setViewState(v); history.replaceState(null, '', v === 'stories' ? '#news' : `#news/${v}`) }
  const [days, setDays] = useState(3)
  const [q, setQ] = useState('')
  const [source, setSource] = useState('')
  const [bySource, setBySource] = useState(false)
  const [mine, setMine] = useState(true) // "For you": only articles that mention a watchlist artist
  const [mentions, setMentions] = useState(false) // also count artists named only in the summary
  const [articles, setArticles] = useState<NewsArticle[] | null>(null)
  const [health, setHealth] = useState<NewsHealth | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.news(days, mine).then(setArticles).catch((e) => setError(String(e)))
    api.newsSources().then(setHealth).catch((e) => setError(String(e)))
  }, [days, mine, refreshKey])

  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return (articles ?? []).filter((a) => (!source || a.source_key === source) &&
      (!mine || mentions || a.artists.some((t) => t.location === 'title')) &&
      (!ql || a.title.toLowerCase().includes(ql) || (a.excerpt ?? '').toLowerCase().includes(ql)))
  }, [articles, q, source, mine, mentions])

  const groups = useMemo(() => {
    const m = new Map<string, NewsArticle[]>()
    for (const a of shown) m.set(a.source_name, [...(m.get(a.source_name) ?? []), a])
    return [...m.entries()].sort((x, y) => y[1].length - x[1].length)
  }, [shown])

  const sources = health?.sources.filter((s) => s.enabled && !s.needs_key) ?? []
  // An artist's own channel failing is routine (no uploads yet, renamed): the Sources panel counts those
  const broken = sources.filter((s) => s.last_error && !s.official)
  const lastCheck = health?.checks[0]
  return (
    <>
      <p className="muted small">
        Headlines from {sources.length} music-news feeds and channels, checked every 30 minutes
        {lastCheck ? ` (last check ${fmtWhen(lastCheck.started_at)}, ${lastCheck.events_fetched} new)` : ''}, tagged
        with your watchlist artists and grouped into stories. Nothing here becomes an idea until you press Send to queue.
      </p>
      {error && <div className="banner error">{error}</div>}
      {broken.length > 0 && (
        <div className="banner">
          Feed problem: {broken.map((s) => `${s.name} (${s.last_error})`).join(' · ')}. Their articles are kept; it's
          retried with a longer wait.
        </div>
      )}

      <div className="toolbar">
        <button className={view === 'stories' ? 'tab active' : 'tab'} onClick={() => setView('stories')}
                title="Stories about your watchlist artists, best first">For you</button>
        <button className={view === 'releases' ? 'tab active' : 'tab'} onClick={() => setView('releases')}
                title="Releases by your watchlist artists that no feed covered, from Deezer and Apple Music (iTunes)">New releases</button>
        <button className={view === 'discover' ? 'tab active' : 'tab'} onClick={() => setView('discover')}
                title="Artists you don't follow yet, with why they're here (experimental)">Discover</button>
        <button className={view === 'articles' ? 'tab active' : 'tab'} onClick={() => setView('articles')}>All headlines</button>
        {view !== 'discover' && <select value={days} onChange={(e) => setDays(+e.target.value)}>
          {[1, 2, 3, 7, 14].map((d) => <option key={d} value={d}>{d === 1 ? 'Last 24 hours' : `Last ${d} days`}</option>)}
        </select>}
      </div>

      {view === 'stories' && <StoriesList refreshKey={refreshKey} days={days} origin="news" />}
      {view === 'releases' && <StoriesList refreshKey={refreshKey} days={days} origin="catalog" />}
      {view === 'discover' && <DiscoverView refreshKey={refreshKey} />}
      {view === 'articles' && <>
      <div className="toolbar">
        <input placeholder="Search headlines" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">All sources</option>
          {sources.filter((s) => !s.official).map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
        </select>
        <label className="small" title="Only articles about your watchlist artists">
          <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> For you
        </label>
        {mine && (
          <label className="small" title="Also articles that only name the artist in the summary (roundups, credits)">
            <input type="checkbox" checked={mentions} onChange={(e) => setMentions(e.target.checked)} /> Include mentions
          </label>
        )}
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
      </>}

      <WatchlistPanel refreshKey={refreshKey} />

      <h3>Sources</h3>
      <SourcesTable sources={health?.sources ?? []} youtube={health?.youtube} />
    </>
  )
}

/** YouTube (M8): whether it's on, today's quota use, and your artists' own channels in one line */
function YouTubeStatus({ sources, youtube }: { sources: NewsSource[]; youtube: NonNullable<NewsHealth['youtube']> }) {
  const own = sources.filter((s) => s.official && s.enabled)
  const read = own.filter((s) => s.last_ok_at)
  const failing = own.filter((s) => s.last_error)
  return (
    <p className="small">
      <strong>YouTube:</strong>{' '}
      {youtube.enabled
        ? <>on, {youtube.units_today} of {youtube.daily_cap} quota units used today.</>
        : <><span className="pill pill-warn">off</span> add a free YOUTUBE_API_KEY to .env (Google Cloud console → YouTube Data API v3).</>}
      {' '}Your artists' own channels (from MusicBrainz): {own.length}
      {youtube.enabled && own.length > 0 && <>, {read.length} read{failing.length > 0 && `, ${failing.length} not found or failing`}</>}.
      {' '}An upload there marks its story <em>confirmed</em>. The channel RSS feeds aren't used: youtube.com's robots.txt disallows them.
    </p>
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
                {a.artists.length > 0 && <div className="trend-pills">{a.artists.map((t) => <ArtistPill key={t.id} t={t} />)}</div>}
                {a.excerpt && <div className="muted small">{a.excerpt}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const CONFIDENCE: Record<NewsArtistTag['confidence'], string> = {
  high: 'named in the headline',
  medium: 'named only in the summary',
  low: 'name is also an ordinary word; counted because the headline has a music word',
}

function ArtistPill({ t }: { t: NewsArtistTag }) {
  const why = [t.match === 'owner' ? 'their own channel' : CONFIDENCE[t.confidence], t.match === 'alias' ? 'matched an alias' : '', `tier: ${t.tier}`,
    t.plays ? `${t.plays} plays` : ''].filter(Boolean).join(' · ')
  return (
    <span className={`pill trend${t.location === 'excerpt' ? ' muted' : ''}`} title={why}>
      {t.location === 'excerpt' ? 'mentions ' : ''}{t.name}{t.confidence === 'low' ? ' ?' : ''}{t.plays ? ` 🎧${t.plays}` : ''}
    </span>
  )
}

function SourcesTable({ sources, youtube }: { sources: NewsSource[]; youtube?: NewsHealth['youtube'] }) {
  return (
    <div className="table-wrap">
      {youtube && <YouTubeStatus sources={sources} youtube={youtube} />}
      <table className="events">
        <thead><tr><th>Source</th><th>Niches</th><th>Articles</th><th>Last 24h</th><th>Last OK</th><th>Last “nothing new”</th><th>Status</th></tr></thead>
        <tbody>
          {sources.filter((s) => !s.official).map((s) => (
            <tr key={s.key} className={s.enabled ? '' : 'muted'}>
              <td><a href={s.url} target="_blank" rel="noreferrer" title={s.terms_note ?? ''}>{s.name}</a></td>
              <td className="small">{s.niches.join(', ')}</td>
              <td>{s.articles}</td>
              <td>{s.last_24h}</td>
              <td className="nowrap">{s.last_ok_at ? fmtWhen(s.last_ok_at) : '–'}</td>
              <td className="nowrap">{s.last_not_modified_at ? fmtWhen(s.last_not_modified_at) : '–'}</td>
              <td>
                {!s.enabled ? 'removed from config'
                  : s.needs_key ? <span className="muted">needs YOUTUBE_API_KEY</span>
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
