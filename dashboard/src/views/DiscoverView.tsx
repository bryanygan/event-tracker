import { useEffect, useMemo, useState } from 'react'
import { api, STATIC, type Candidate, type DiscoverAction, type DiscoverList } from '../api'
import { fmtWhen } from '../App'

// Discover (tracker/discovery.py, roadmap M7, experimental): artists you don't follow yet, each with the evidence that
// put them here. Momentum only shows once its sample-size rules are met, and "breakout" needs two signals that agree.
const STATUS_LABEL: Record<Candidate['status'], string> = { new: 'New', accepted: 'Added', dismissed: 'Not interested' }

const fmtNum = (n: number | undefined) => (n === undefined ? null : n.toLocaleString())

export default function DiscoverView({ refreshKey, onQueued }: { refreshKey: number; onQueued?: () => void }) {
  const [status, setStatus] = useState<Candidate['status']>('new')
  const [data, setData] = useState<DiscoverList | null>(null)
  const [q, setQ] = useState('')
  const [breakoutOnly, setBreakoutOnly] = useState(false)
  const [picked, setPicked] = useState<number[]>([])
  const [open, setOpen] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = () => api.discover(status).then(setData).catch((e) => setError(String(e)))
  useEffect(() => { load() }, [status, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return (data?.candidates ?? []).filter((c) => (!breakoutOnly || c.breakout) &&
      (!ql || c.name.toLowerCase().includes(ql) || c.tags.some((t) => t.toLowerCase().includes(ql)) ||
        c.similar_to.some((s) => s.toLowerCase().includes(ql))))
  }, [data, q, breakoutOnly])

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setError(null); setNotice(null)
    try { await fn(); if (done) setNotice(done); await load() } catch (e) { setError(String(e)) }
  }
  const decide = (c: Candidate, action: DiscoverAction) => act(() => api.decideCandidate(c.id, action),
    action === 'watch' || action === 'adjacent' ? `${c.name} is on your watchlist (${action}); news about them starts matching` : undefined)
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  const makeIdea = () => act(async () => { await api.candidatesIdea(picked); setPicked([]); onQueued?.() },
    'Sent to What to post as an “Artists to watch” idea')

  return (
    <>
      <p className="muted small">
        <span className="pill">experimental</span> Artists you don't follow yet: Last.fm's similar artists for your
        most-played, the top artists in your niche tags, and guests on your artists' releases. Rebuilt weekly
        {data?.refreshed_at ? ` (last ${fmtWhen(data.refreshed_at)})` : ''}. Momentum is growth against each artist's own
        numbers, from daily snapshots: it needs {data?.rules.min_observations ?? 3}+ snapshots over {data?.rules.min_days ?? 7}+
        days, and an artist is only marked as breaking out when two signals agree.
      </p>
      {data && !data.enabled && <div className="banner">LAST_FM_API_KEY isn't set in .env, so discovery is paused.</div>}
      {error && <div className="banner error">{error}</div>}
      {notice && <div className="draft-done" role="status">✓ {notice}</div>}
      <div className="toolbar">
        <select value={status} onChange={(e) => { setStatus(e.target.value as Candidate['status']); setPicked([]) }}>
          {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input placeholder="Search artists, tags or similar-to" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="small" title="Only artists growing on two signals (experimental)">
          <input type="checkbox" style={{ width: 'auto' }} checked={breakoutOnly} onChange={(e) => setBreakoutOnly(e.target.checked)} /> Breakout only
        </label>
        <span className="muted small">{data ? `${shown.length} artists` : 'Loading…'}</span>
        {!STATIC && picked.length > 0 && (
          <button disabled={picked.length < 2 || picked.length > 8} onClick={makeIdea}
                  title="A video idea in What to post: one slide per artist, with why they're here as the facts">
            {picked.length < 2 ? 'Pick at least 2 for a video idea' : picked.length > 8 ? 'Pick up to 8' : `Make an “Artists to watch” idea (${picked.length})`}
          </button>
        )}
      </div>
      {data && shown.length === 0 && (
        <p className="muted">{status === 'new' ? 'No candidates yet: the first weekly refresh runs with the next news check.' : 'None here.'}</p>
      )}
      {shown.map((c) => (
        <div key={c.id} className={`card${status === 'dismissed' ? ' thin' : ''}`}>
          <div className="card-head">
            {!STATIC && <input type="checkbox" style={{ width: 'auto' }} checked={picked.includes(c.id)} onChange={() => toggle(c.id)}
                               aria-label={`pick ${c.name} for a video idea`} />}
            <div>
              <div className="trend-pills">
                {c.breakout && <span className="pill band strong" title="two signals growing against their own baseline">Breakout watch · experimental</span>}
                {c.tags.slice(0, 4).map((t) => <span key={t} className="pill">{t}</span>)}
              </div>
              <strong>{c.lastfm_url ? <a href={c.lastfm_url} target="_blank" rel="noreferrer">{c.name}</a> : c.name}</strong>
              <div className="muted small">
                {[fmtNum(c.metrics.deezer_fans) && `${fmtNum(c.metrics.deezer_fans)} Deezer fans`,
                  fmtNum(c.metrics.lastfm_listeners) && `${fmtNum(c.metrics.lastfm_listeners)} Last.fm listeners`]
                  .filter(Boolean).join(' · ') || 'No audience numbers yet'}
              </div>
            </div>
            <span className="muted small nowrap" style={{ marginLeft: 'auto' }}>found {fmtWhen(c.first_seen)}</span>
          </div>
          <ul className="reasons small">{c.why.map((w, i) => <li key={i}>{w}</li>)}</ul>
          <p className="muted small">Momentum: {c.momentum ? c.momentum.summary : 'Collecting: needs a week of daily snapshots'}</p>
          <div className="actions">
            {!STATIC && (status === 'new' ? <>
              <button onClick={() => decide(c, 'watch')} title="Add to your watchlist: their news shows up in For you">Add to watchlist</button>
              <button className="ghost" onClick={() => decide(c, 'adjacent')} title="Add as adjacent: matched, but scored a little lower than your own artists">Add as adjacent</button>
              <button className="ghost" onClick={() => decide(c, 'dismiss')}>Not interested</button>
            </> : status === 'dismissed' ? <button className="ghost" onClick={() => decide(c, 'restore')}>Restore</button> : null)}
            {c.momentum && <button className="ghost" onClick={() => setOpen(open === c.id ? null : c.id)}>
              {open === c.id ? 'Hide signals' : 'Signals'}</button>}
          </div>
          {open === c.id && c.momentum && (
            <table className="small">
              <tbody>
                {c.momentum.signals.map((s) => (
                  <tr key={`${s.provider}-${s.metric}`} className={s.ready ? '' : 'thin'}>
                    <td>{s.label}</td><td className="nowrap">{s.status === 'ready' ? (s.growing ? 'growing' : 'steady') : s.status.replace('_', ' ')}</td>
                    <td>{s.why}{s.counts ? '' : ' (supplementary: never counts toward breakout)'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </>
  )
}
