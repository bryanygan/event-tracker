import { useEffect, useState } from 'react'
import { api, type Alert, type Draft, type Job, type ShowFacts } from '../api'

const KIND_LABEL: Record<Alert['kind'], string> = {
  new_tour: 'Just announced',
  presale_soon: 'Presale / on-sale',
  weekend: 'This weekend',
  stacked_month: 'Stacked month',
  status_change: 'Status change',
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
        <select value={filter} onChange={(e) => setFilter(e.target.value as 'open' | 'dismissed')}>
          <option value="open">Open</option>
          <option value="dismissed">Dismissed</option>
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value as Alert['kind'] | '')}>
          <option value="">All types</option>
          {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="muted small">{shown.length} alerts</span>
      </div>
      {shown.length === 0 && <p className="muted">Nothing here yet. Alerts show up after a pipeline run finds something.</p>}
      {shown.map((a) => (
        <AlertCard key={a.id} alert={a} busy={busy}
          onRegenerate={async () => onJob(await api.regenerate(a.id))}
          onStatus={async (s) => { await api.setAlertStatus(a.id, s); load(); onChange() }}
          onDraftChange={() => { load(); onChange() }} />
      ))}
    </>
  )
}

function AlertCard({ alert, busy, onRegenerate, onStatus, onDraftChange }: {
  alert: Alert; busy: boolean; onRegenerate: () => void
  onStatus: (s: Alert['status']) => void; onDraftChange: () => void
}) {
  const [showOlder, setShowOlder] = useState(false)
  const shows: ShowFacts[] = alert.facts.shows ?? (alert.facts.show ? [alert.facts.show] : [])
  const [latest, ...older] = alert.drafts

  return (
    <section className={`card kind-${alert.kind}`}>
      <div className="card-head">
        <span className="pill">{KIND_LABEL[alert.kind]}</span>
        <h2>{alert.title}</h2>
        <span className="muted small" title="notability score">★ {Math.round(alert.score)}</span>
      </div>

      <details className="facts">
        <summary>Facts ({shows.length} show{shows.length === 1 ? '' : 's'}) — the only things the draft is allowed to say</summary>
        <table>
          <tbody>
            {shows.map((s, i) => (
              <tr key={i}>
                <td>{s.date}{s.time ? ` · ${s.time}` : ''}</td>
                <td><strong>{s.artist ?? s.event_name}</strong>{s.support ? <span className="muted"> w/ {s.support}</span> : null}</td>
                <td>{s.venue}</td>
                <td>{s.price ?? ''}</td>
                <td>{s.url && <a href={s.url} target="_blank" rel="noreferrer">tickets ↗</a>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <pre>{JSON.stringify(Object.fromEntries(Object.entries(alert.facts).filter(([k]) => k !== 'shows' && k !== 'show')), null, 2)}</pre>
      </details>

      {latest ? <DraftView draft={latest} onChange={onDraftChange} /> : <p className="muted">No draft yet.</p>}
      {older.length > 0 && (
        <button className="link" onClick={() => setShowOlder(!showOlder)}>
          {showOlder ? 'Hide' : 'Show'} {older.length} older draft{older.length > 1 ? 's' : ''}
        </button>
      )}
      {showOlder && older.map((d) => <DraftView key={d.id} draft={d} onChange={onDraftChange} />)}

      <div className="actions">
        <button onClick={onRegenerate} disabled={busy}>{latest ? 'New draft' : 'Write draft'}</button>
        {alert.status !== 'dismissed'
          ? <button className="ghost" onClick={() => onStatus('dismissed')}>Dismiss</button>
          : <button className="ghost" onClick={() => onStatus(alert.drafts.length ? 'drafted' : 'new')}>Restore</button>}
      </div>
    </section>
  )
}

function DraftView({ draft, onChange }: { draft: Draft; onChange: () => void }) {
  const [editing, setEditing] = useState(false)
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
        <span className="muted small">#{draft.id} · {draft.model} · {new Date(draft.created_at).toLocaleString()}</span>
        <CopyButton label="Copy caption + tags" text={`${fields.caption}\n\n${fields.hashtags}`} />
        {editing
          ? <><button onClick={save}>Save</button><button className="ghost" onClick={() => setEditing(false)}>Cancel</button></>
          : <button className="ghost" onClick={() => setEditing(true)}>Edit</button>}
        <select value={draft.status} onChange={(e) => setStatus(e.target.value as Draft['status'])}>
          <option value="pending">Pending review</option>
          <option value="approved">Approved</option>
          <option value="filmed">Filmed</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>
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
