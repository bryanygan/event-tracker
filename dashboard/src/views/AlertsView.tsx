import { useEffect, useState } from 'react'
import { api, STATIC, type Alert, type ContextUsage, type Draft, type Job, type ShowFacts, type WebContext } from '../api'
import { fmtWhen } from '../App'
import { PostForm } from './PostsView'

export const KIND_LABEL: Record<Alert['kind'], string> = {
  new_tour: 'Just announced',
  presale_soon: 'Presale / on-sale',
  weekend: 'This weekend',
  stacked_month: 'Stacked month',
  status_change: 'Status change',
  team_week: 'Home games this week',
  big_game: 'Big game',
}

const STATUS_LABEL: Record<Draft['status'], string> = {
  pending: 'Pending review',
  approved: 'Approved',
  filmed: 'Filmed',
  rejected: 'Rejected',
}

type Props = { refreshKey: number; busy: boolean; onJob: (j: Job) => void; onChange: () => void }

export default function AlertsView({ refreshKey, busy, onJob, onChange }: Props) {
  const [filter, setFilter] = useState<'open' | 'dismissed'>('open')
  const [kind, setKind] = useState<Alert['kind'] | ''>('')
  const [alerts, setAlerts] = useState<Alert[] | null>(null)

  const load = () => api.alerts(filter).then(setAlerts)
  useEffect(() => { load() }, [filter, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!alerts) return <p className="muted">Loading…</p>
  const shown = alerts.filter((a) => !kind || a.kind === kind)

  return (
    <>
      <div className="toolbar">
        {!STATIC && (  // the snapshot only contains open alerts
          <select value={filter} onChange={(e) => setFilter(e.target.value as 'open' | 'dismissed')}>
            <option value="open">Open</option>
            <option value="dismissed">Dismissed</option>
          </select>
        )}
        <select value={kind} onChange={(e) => setKind(e.target.value as Alert['kind'] | '')}>
          <option value="">All types</option>
          {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="muted small">{shown.length} alerts</span>
      </div>
      {shown.length === 0 && <p className="muted">Nothing here yet. Alerts show up after a pipeline run finds something.</p>}
      {shown.map((a) => (
        <AlertCard key={a.id} alert={a} busy={busy}
          onRegenerate={async (angle) => onJob(await api.regenerate(a.id, angle))}
          onStatus={async (s) => { await api.setAlertStatus(a.id, s); load(); onChange() }}
          onDraftChange={() => { load(); onChange() }} />
      ))}
    </>
  )
}

// "**Oct. 13:** Tour announced.[1]" -> bold dates and [n] links to the cited source, without raw HTML.
function ContextLine({ line, sources }: { line: string; sources: WebContext['sources'] }) {
  const parts = line.replace(/^\s*[-*]\s+/, '').split(/(\*\*.+?\*\*|\*[^*\s][^*]*\*|\[\d+\])/g)
  return (
    <li>
      {parts.map((p, i) => {
        const bold = p.match(/^\*\*(.+)\*\*$/) // may hold an *italic* title: drop those marks
        if (bold) return <strong key={i}>{bold[1].replace(/\*/g, '')}</strong>
        const italic = p.match(/^\*([^*]+)\*$/)
        if (italic) return <em key={i}>{italic[1]}</em>
        const cite = p.match(/^\[(\d+)\]$/)
        if (cite) {
          const s = sources.find((x) => x.id === Number(cite[1]))
          return s ? <sup key={i}><a href={s.url} target="_blank" rel="noreferrer" title={s.title ?? s.url}>[{cite[1]}]</a></sup> : null
        }
        return p
      })}
    </li>
  )
}

function WebContextView({ alertId, initial }: { alertId: number; initial: WebContext | null }) {
  const [ctx, setCtx] = useState(initial)
  const [usage, setUsage] = useState<ContextUsage | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setCtx(initial), [initial])

  const look = async (refresh: boolean) => {
    setBusy(true); setError(null)
    try {
      const r = await api.lookupContext(alertId, refresh)
      setCtx(r.context); setUsage(r.usage)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setBusy(false)
    }
  }
  const lines = ctx?.answer.split('\n').filter((l) => l.trim()) ?? []

  return (
    <details className="web-context" open={!!ctx}>
      <summary>Web context: what's new, from the web{ctx ? ` (looked up ${fmtWhen(ctx.created_at)}${ctx.fresh ? '' : ', over a week old'})` : ''}</summary>
      {ctx ? (
        <>
          <p className="muted small">From Perplexity search. Not checked facts: open the sources before you say any of this on camera.</p>
          <ul>{lines.map((l, i) => <ContextLine key={i} line={l} sources={ctx.sources} />)}</ul>
          <ol className="sources small">
            {ctx.sources.map((s) => (
              <li key={s.id} value={s.id}>
                <a href={s.url} target="_blank" rel="noreferrer">{s.title ?? s.url}</a>
                <span className="muted"> · {new URL(s.url).hostname.replace(/^www\./, '')}{s.date ? ` · ${s.date}` : ''}</span>
              </li>
            ))}
          </ol>
        </>
      ) : <p className="muted small">No lookup yet.</p>}
      {!STATIC && (
        <div className="row">
          <button className="ghost" disabled={busy} onClick={() => look(!!ctx)}
                  title="one Perplexity search, about $0.002; reused for 7 days">
            {busy ? 'Looking up…' : ctx ? 'Look up again' : 'Look up context'}
          </button>
          {usage && <span className="muted small">{usage.today} of {usage.cap} lookups used today</span>}
          {error && <span className="warn small">{error}</span>}
        </div>
      )}
    </details>
  )
}

export function AlertCard({ alert, busy, onRegenerate, onStatus, onDraftChange }: {
  alert: Alert; busy: boolean; onRegenerate: (angle?: string) => void
  onStatus: (s: Alert['status']) => void; onDraftChange: () => void
}) {
  const [showOlder, setShowOlder] = useState(false)
  const shows: ShowFacts[] = alert.facts.shows ?? (alert.facts.show ? [alert.facts.show] : [])
  // Roundups: the "my N picks" cut (best by notability), starred in the facts table
  const picks = ((alert.facts.picks as ShowFacts[] | undefined) ?? []).map((s) => `${s.artist}|${s.date}`)
  const [latest, ...older] = alert.drafts

  return (
    <section className={`card kind-${alert.kind}`}>
      <div className="card-head">
        <span className="pill">{KIND_LABEL[alert.kind]}</span>
        <h2>{alert.title}</h2>
        {Number(alert.facts.your_plays) > 0 && (
          <span className="pill new" title="your plays in the last 2 years">🎧 {String(alert.facts.your_plays)} plays</span>
        )}
        {alert.posts.map((p) => (
          <span key={p.id} className="pill new" title={`logged as posted ${fmtWhen(p.posted_at)}`}>
            {p.url ? <a href={p.url} target="_blank" rel="noreferrer">Posted ✓</a> : 'Posted ✓'}
          </span>
        ))}
        <span className="muted small" title="notability score">★ {Math.round(alert.score)}</span>
      </div>
      {alert.updated_at && <p className="muted small">Facts refreshed {fmtWhen(alert.updated_at)} (new shows, cancellations or sale changes).</p>}

      <details className="facts">
        <summary>Facts ({shows.length} show{shows.length === 1 ? '' : 's'}) — the only things the draft is allowed to say</summary>
        <table>
          <tbody>
            {shows.map((s, i) => (
              <tr key={i}>
                <td>{s.date}{s.time ? ` · ${s.time}` : ''}</td>
                <td>
                  {picks.includes(`${s.artist}|${s.date}`) && <span className="pick" title={`one of the top ${picks.length} picks`}>★ </span>}
                  <strong>{s.artist ?? s.event_name}</strong>{s.support ? <span className="muted"> w/ {s.support}</span> : null}</td>
                <td>{s.venue}</td>
                <td>{s.price ?? ''}</td>
                <td>{s.url && <a href={s.url} target="_blank" rel="noreferrer">tickets ↗</a>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <pre>{JSON.stringify(Object.fromEntries(Object.entries(alert.facts).filter(([k]) => !['shows', 'show', 'your_plays', 'picks'].includes(k))), null, 2)}</pre>
      </details>

      {alert.context_supported && <WebContextView alertId={alert.id} initial={alert.context} />}

      {alert.angles.length > 0 && (
        <details className="angles" open={!latest}>
          <summary>Angles ({alert.angles.length}): pick one to draft from</summary>
          {alert.angles.map((a) => (
            <div key={a.key} className="angle">
              <div>
                <span className="pill">{a.label}</span> <strong>“{a.hook}”</strong>
                <div className="muted small">{a.pitch} · {a.style} · CTA: {a.cta}</div>
              </div>
              {!STATIC && <button className="ghost" disabled={busy} onClick={() => onRegenerate(a.key)}
                                  title={['presale', 'status_change', 'team_week', 'big_game'].includes(String(alert.facts.format))
                                    ? 'templated format: drafted instantly, no model' : 'the model writes the script around this hook (~1 min)'}>
                Draft this
              </button>}
            </div>
          ))}
        </details>
      )}

      {latest ? <DraftView draft={latest} alertId={alert.id} onChange={onDraftChange} /> : <p className="muted">No draft yet.</p>}
      {older.length > 0 && (
        <button className="link" onClick={() => setShowOlder(!showOlder)}>
          {showOlder ? 'Hide' : 'Show'} {older.length} older draft{older.length > 1 ? 's' : ''}
        </button>
      )}
      {showOlder && older.map((d) => <DraftView key={d.id} draft={d} alertId={alert.id} onChange={onDraftChange} />)}

      {!STATIC && <div className="actions">
        <button onClick={() => onRegenerate()} disabled={busy}>{latest ? 'New draft' : 'Write draft'}</button>
        {alert.status !== 'dismissed'
          ? <button className="ghost" onClick={() => onStatus('dismissed')}>Dismiss</button>
          : <button className="ghost" onClick={() => onStatus(alert.drafts.length ? 'drafted' : 'new')}>Restore</button>}
      </div>}
    </section>
  )
}

function DraftView({ draft, alertId, onChange }: { draft: Draft; alertId: number; onChange: () => void }) {
  const [editing, setEditing] = useState(false)
  const [logging, setLogging] = useState(false)
  const [fields, setFields] = useState({ hook: draft.hook, script: draft.script, caption: draft.caption, hashtags: draft.hashtags })

  const save = async () => { await api.updateDraft(draft.id, fields); setEditing(false); onChange() }
  const setStatus = async (status: Draft['status']) => { await api.updateDraft(draft.id, { status }); onChange() }

  const block = (label: string, key: keyof typeof fields, rows = 2) => (
    <div className="field">
      <div className="field-head">
        <label>{label}</label>
        <CopyButton text={fields[key]} />
      </div>
      {editing
        ? <textarea rows={rows} value={fields[key]} onChange={(e) => setFields({ ...fields, [key]: e.target.value })} />
        : <p className={key === 'hook' ? 'hook' : ''}>{fields[key]}</p>}
    </div>
  )

  return (
    <div className={`draft status-${draft.status}`}>
      {draft.facts_changes.length > 0 && (
        <div className="warn">
          ↻ The facts changed since this draft was written. Edit it or write a new draft:
          <ul>{draft.facts_changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      {draft.warnings.length > 0 && (
        <div className="warn">⚠ Check before filming: {draft.warnings.join(' · ')}</div>
      )}
      {block('Hook', 'hook')}
      {block('Script (voiceover)', 'script', 6)}
      <div className="field">
        <div className="field-head">
          <label>On-screen text (from data)</label>
          <CopyButton text={draft.onscreen.join('\n')} />
        </div>
        <ul className="onscreen">{draft.onscreen.map((l, i) => <li key={i}>{l}</li>)}</ul>
      </div>
      {block('Caption', 'caption', 3)}
      {block('Hashtags', 'hashtags', 1)}
      <div className="draft-foot">
        <span className="muted small">#{draft.id} · {draft.model}{draft.angle ? ` · ${draft.angle} angle` : ''} · {new Date(draft.created_at).toLocaleString()}</span>
        <CopyButton label="Copy caption + tags" text={`${fields.caption}\n\n${fields.hashtags}`} />
        {STATIC ? <span className="pill">{STATUS_LABEL[draft.status]}</span> : <>
          {editing
            ? <><button onClick={save}>Save</button><button className="ghost" onClick={() => setEditing(false)}>Cancel</button></>
            : <button className="ghost" onClick={() => setEditing(true)}>Edit</button>}
          <select value={draft.status} onChange={(e) => setStatus(e.target.value as Draft['status'])}>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          {!logging && <button className="ghost" onClick={() => setLogging(true)}>Log as posted</button>}
        </>}
      </div>
      {logging && <PostForm alertId={alertId} draftId={draft.id} hook={fields.hook}
                            onDone={() => { setLogging(false); onChange() }} onCancel={() => setLogging(false)} />}
    </div>
  )
}

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button className="copy" onClick={async () => {
      await navigator.clipboard.writeText(text)
      setDone(true)
      setTimeout(() => setDone(false), 1200)
    }}>{done ? 'Copied ✓' : label}</button>
  )
}
