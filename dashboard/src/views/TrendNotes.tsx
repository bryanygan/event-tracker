import { useEffect, useState } from 'react'
import { api, STATIC, TREND_CONFIDENCE, TREND_KINDS, TREND_SCOPES, type TrendMatch, type TrendNote } from '../api'
import { fmtWhen } from '../App'

// Recomputed here so a snapshot exported days ago still shows which notes have run out.
const isExpired = (n: { expires_at: string }) => new Date(n.expires_at) <= new Date()

const SCOPE_LABEL = { philly: 'Philly', national: 'National', unknown: 'Scope ?' } as const

// "What to post" panel: trends you noticed, typed in by hand. Collapsed by default to keep the queue tidy.
export default function TrendNotes({ onChange }: { onChange: () => void }) {
  const [notes, setNotes] = useState<TrendNote[]>([])
  const [adding, setAdding] = useState(false)
  const [showExpired, setShowExpired] = useState(false)
  const load = () => { api.trends().then(setNotes) }
  useEffect(() => { load() }, [])
  const changed = () => { load(); onChange() }

  const active = notes.filter((n) => !isExpired(n))
  const expired = notes.filter(isExpired)

  return (
    <details className="trend-panel">
      <summary>
        Trend notes ({active.length} active) <span className="muted small">· what you saw trending; feeds “search demand”</span>
      </summary>
      {notes.length === 0 && <p className="muted small">None yet. Add a sound, search or hashtag you saw, and the artists, genres or venues it relates to.</p>}
      {active.map((n) => <NoteRow key={n.id} note={n} onChange={changed} />)}
      {expired.length > 0 && (
        <button className="link small" onClick={() => setShowExpired(!showExpired)}>
          {showExpired ? 'Hide' : 'Show'} {expired.length} expired
        </button>
      )}
      {showExpired && expired.map((n) => <NoteRow key={n.id} note={n} onChange={changed} />)}
      {!STATIC && (adding
        ? <TrendForm onDone={() => { setAdding(false); changed() }} onCancel={() => setAdding(false)} />
        : <div className="actions"><button className="ghost" onClick={() => setAdding(true)}>+ Add trend note</button></div>)}
    </details>
  )
}

function NoteRow({ note, onChange }: { note: TrendNote; onChange: () => void }) {
  const [confirm, setConfirm] = useState(false)
  const old = isExpired(note)
  return (
    <div className={`trend-row${old ? ' thin' : ''}`}>
      <div className="trend-main">
        <span className="pill">{note.kind}</span>{' '}
        <strong>{note.text}</strong>
        {note.source_url && <> <a href={note.source_url} target="_blank" rel="noreferrer noopener">source ↗</a></>}
        <div className="muted small">
          {SCOPE_LABEL[note.scope]} · {note.confidence} confidence{note.momentum ? ` · ${note.momentum}` : ''}
          {' · '}matches: {note.terms.join(', ')}
          {' · '}seen {fmtWhen(note.observed_at)} · {old ? `expired ${fmtWhen(note.expires_at)}` : `until ${fmtWhen(note.expires_at)}`}
        </div>
        {note.notes && <div className="small">{note.notes}</div>}
      </div>
      {!STATIC && (confirm
        ? <span className="actions-inline">
            <button className="ghost" onClick={async () => { await api.deleteTrend(note.id); onChange() }}>Delete</button>
            <button className="ghost" onClick={() => setConfirm(false)}>Keep</button>
          </span>
        : <button className="ghost" title="delete this note" onClick={() => setConfirm(true)}>✕</button>)}
    </div>
  )
}

function TrendForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [kind, setKind] = useState<TrendNote['kind']>('search')
  const [text, setText] = useState('')
  const [terms, setTerms] = useState('')
  const [scope, setScope] = useState<TrendNote['scope']>('unknown')
  const [confidence, setConfidence] = useState<TrendNote['confidence']>('medium')
  const [sourceUrl, setSourceUrl] = useState('')
  const [days, setDays] = useState('7')
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    setError(null)
    const now = new Date()
    const d = Number(days)
    try {
      await api.createTrend({
        kind, text, scope, confidence,
        terms: terms.split(',').map((t) => t.trim()).filter(Boolean),
        source_url: sourceUrl.trim() || null,
        observed_at: now.toISOString(),
        ...(d > 0 ? { expires_at: new Date(+now + d * 864e5).toISOString() } : {}),
      })
      onDone()
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  return (
    <form className="trend-form" onSubmit={(e) => { e.preventDefault(); save() }}>
      <div className="form-row">
        <select value={kind} onChange={(e) => setKind(e.target.value as TrendNote['kind'])} aria-label="kind">
          {TREND_KINDS.map((k) => <option key={k}>{k}</option>)}
        </select>
        <input className="grow" maxLength={200} placeholder="What you saw, e.g. “philly concerts this weekend”" value={text}
               onChange={(e) => setText(e.target.value)} />
      </div>
      <input maxLength={400} placeholder="Matches (comma-separated): artist, genre, venue or keyword" value={terms}
             onChange={(e) => setTerms(e.target.value)} />
      <div className="form-row">
        <select value={scope} onChange={(e) => setScope(e.target.value as TrendNote['scope'])} aria-label="scope">
          {TREND_SCOPES.map((s) => <option key={s} value={s}>{s === 'unknown' ? 'scope unknown' : s}</option>)}
        </select>
        <select value={confidence} onChange={(e) => setConfidence(e.target.value as TrendNote['confidence'])} aria-label="confidence">
          {TREND_CONFIDENCE.map((c) => <option key={c} value={c}>{c} confidence</option>)}
        </select>
        <label className="small muted">counts for <input className="days" type="number" min={1} max={60} value={days}
                                                          onChange={(e) => setDays(e.target.value)} /> days</label>
      </div>
      <input type="url" maxLength={500} placeholder="Source link (optional)" value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} />
      <div className="actions-inline">
        <button type="submit">Save note</button>
        <button type="button" className="ghost" onClick={onCancel}>Cancel</button>
      </div>
      {error && <p className="warn small">{error}</p>}
    </form>
  )
}

// Matched notes on a queue card: one pill per live note (scope always spelled out), expired ones only counted.
export function TrendPills({ matches }: { matches: TrendMatch[] }) {
  if (!matches?.length) return null
  const live = matches.filter((m) => !m.expired && !isExpired(m))
  const old = matches.length - live.length
  return (
    <p className="trend-pills small">
      {live.map((m) => (
        <span key={m.id} className="pill trend"
              title={`${m.confidence} confidence · matched “${m.matched_term}”${m.match === 'title' ? ' in the title' : ''} · seen ${fmtWhen(m.observed_at)}`}>
          📈 {SCOPE_LABEL[m.scope]} {m.kind}: {m.text}
        </span>
      ))}
      {old > 0 && <span className="muted" title="expired trend notes no longer count">+{old} expired trend note{old > 1 ? 's' : ''}</span>}
    </p>
  )
}
