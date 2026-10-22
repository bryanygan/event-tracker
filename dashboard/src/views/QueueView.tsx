import { useEffect, useState } from 'react'
import { api, STATIC, type Alert, type Job, type Queue, type QueueItem, type Stage, type Summary } from '../api'
import { fmtWhen } from '../App'
import { AlertCard, KIND_LABEL } from './AlertsView'

const STAGE_LABEL: Record<Stage, string> = {
  new: 'New idea',
  shortlisted: 'Shortlisted',
  review: 'Draft to review',
  needs_refresh: 'Draft out of date',
  approved: 'Ready to film',
  filmed: 'Filmed',
  posted: 'Posted',
  measured: 'Posted · has numbers',
  snoozed: 'Later',
  expired: 'Expired',
  dismissed: 'Dismissed',
}

const BAND_LABEL = { strong: 'Strong', medium: 'Medium', low: 'Low' } as const

type Filter = 'active' | 'shortlisted' | 'snoozed' | 'posted' | 'expired' | 'dismissed' | 'all'
const FILTERS: Record<Filter, (i: QueueItem) => boolean> = {
  active: (i) => ['new', 'shortlisted', 'review', 'needs_refresh', 'approved', 'filmed'].includes(i.stage),
  shortlisted: (i) => i.shortlisted && !['dismissed', 'expired'].includes(i.stage),
  snoozed: (i) => i.stage === 'snoozed',
  posted: (i) => i.stage === 'posted' || i.stage === 'measured',
  expired: (i) => i.stage === 'expired',
  dismissed: (i) => i.stage === 'dismissed',
  all: () => true,
}

function timeLeft(iso: string) {
  const h = (+new Date(iso) - Date.now()) / 36e5
  if (h < 0) return 'past'
  return h < 48 ? `${Math.round(h)}h left` : `${Math.round(h / 24)} days left`
}

type Props = { refreshKey: number; busy: boolean; summary: Summary | null; onJob: (j: Job) => void; onChange: () => void }

export default function QueueView({ refreshKey, busy, summary, onJob, onChange }: Props) {
  const [queue, setQueue] = useState<Queue | null>(null)
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [filter, setFilter] = useState<Filter>('active')
  const load = () => {
    api.queue().then(setQueue)
    api.alerts(STATIC ? 'open' : 'all').then(setAlerts)
  }
  useEffect(() => { load() }, [refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const changed = () => { load(); onChange() }

  if (!queue) return <p className="muted">Loading…</p>
  const shown = queue.items.filter(FILTERS[filter])
  const c = queue.counts

  return (
    <>
      <div className="queue-counts">
        <Count label="new ideas" value={c.new} />
        <Count label="post within 48h" value={c.time_sensitive} hot={c.time_sensitive > 0} />
        <Count label="drafts to review" value={c.to_review} />
        <Count label="ready to film" value={c.ready_to_film} />
        <Count label="posted this week" value={summary?.posts_this_week} />
      </div>
      <div className="toolbar">
        <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
          {Object.keys(FILTERS).map((f) => <option key={f} value={f}>{f[0].toUpperCase() + f.slice(1)}</option>)}
        </select>
        <span className="muted small">
          {shown.length} ideas · ranked by an <strong>experimental</strong> opportunity score (rules {queue.rules_version}):
          audience, timing, story, Philly/your lane, readiness. Search demand isn’t measured yet, so it’s left out
          rather than guessed. It’s a hint for what to make next, not a view prediction.
        </span>
      </div>
      {shown.length === 0 && <p className="muted">Nothing here.</p>}
      {shown.map((i) => (
        <QueueCard key={i.alert_id} item={i} alert={alerts.find((a) => a.id === i.alert_id)} busy={busy}
                   onJob={onJob} onChange={changed} />
      ))}
    </>
  )
}

function Count({ label, value, hot = false }: { label: string; value?: number; hot?: boolean }) {
  return <div className={hot ? 'stat hot' : 'stat'}><strong>{value ?? '–'}</strong><span>{label}</span></div>
}

function QueueCard({ item, alert, busy, onJob, onChange }: {
  item: QueueItem; alert?: Alert; busy: boolean; onJob: (j: Job) => void; onChange: () => void
}) {
  const [open, setOpen] = useState(false)
  const [editingNote, setEditingNote] = useState(false)
  const [note, setNote] = useState(item.note ?? '')
  const patch = async (fields: Parameters<typeof api.updateAlert>[1]) => { await api.updateAlert(item.alert_id, fields); onChange() }
  const snoozeDay = () => patch({ snoozed_until: new Date(Date.now() + 864e5).toISOString() })
  const saveNote = async () => { await patch({ note: note.trim() || null }); setEditingNote(false) }

  return (
    <section className={`card queue-card kind-${item.kind} band-${item.band ?? 'none'}`}>
      <div className="card-head">
        {item.band && <span className={`pill band ${item.band}`}>{BAND_LABEL[item.band]}</span>}
        <span className="pill">{KIND_LABEL[item.kind]}</span>
        <span className="pill">{STAGE_LABEL[item.stage]}</span>
        {item.post_by && !['expired', 'dismissed', 'posted', 'measured'].includes(item.stage) && (
          <span className={`pill ${timeLeft(item.post_by) === 'past' ? '' : 'due'}`} title={item.expires_at ? `stale after ${fmtWhen(item.expires_at)}` : ''}>
            Post by {fmtWhen(item.post_by)} · {timeLeft(item.post_by)}
          </span>
        )}
        {item.shortlisted && <span className="pill new">★ shortlisted</span>}
      </div>
      <h2 className="queue-title">
        {item.title}
        {item.your_plays > 0 && <span className="pill new" title="your plays in the last 2 years">🎧 {item.your_plays} plays</span>}
      </h2>
      {item.draft?.hook
        ? <p className="hook-used">“{item.draft.hook}”</p>
        : alert?.angles[0] && <p className="hook-used muted" title="suggested angle; open to pick one">“{alert.angles[0].hook}”</p>}
      {item.reasons.length > 0 && <ul className="reasons">{item.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
      {item.caveats.length > 0 && <div className="warn"><ul>{item.caveats.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}
      {item.posts.map((p) => (
        <p key={p.id} className="muted small">
          Posted {fmtWhen(p.posted_at)}{p.url && <> · <a href={p.url} target="_blank" rel="noreferrer">video ↗</a></>}
        </p>
      ))}
      {editingNote
        ? <div className="actions-inline"><input className="grow" value={note} onChange={(e) => setNote(e.target.value)} />
            <button onClick={saveNote}>Save</button><button className="ghost" onClick={() => setEditingNote(false)}>Cancel</button></div>
        : item.note && <p className="note">📝 {item.note}</p>}

      <details className="facts">
        <summary>
          Opportunity {item.score ?? '–'} <span className="muted">(experimental, {Math.round(item.coverage * 100)}% of signals known)</span>
          {' · '}Notability {Math.round(item.notability)}
        </summary>
        <table>
          <tbody>
            {item.components.map((c) => (
              <tr key={c.key} className={c.value === null ? 'thin' : ''}>
                <td>{c.label}</td>
                <td className="nowrap">{c.value ?? 'unknown'}</td>
                <td className="muted nowrap">× {c.weight}</td>
                <td>{c.why}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {item.links.length > 0 && (
          <p className="small">Sources: {item.links.map((l, i) => (
            <span key={i}>{i > 0 && ' · '}<a href={l.url} target="_blank" rel="noreferrer">{l.label} ↗</a></span>
          ))}</p>
        )}
      </details>

      <div className="actions">
        {alert && <button onClick={() => setOpen(!open)}>{open ? 'Hide draft' : item.draft ? 'Open draft' : 'Facts & draft'}</button>}
        {!STATIC && <>
          <button className="ghost" onClick={() => patch({ shortlisted: !item.shortlisted })}>
            {item.shortlisted ? '★ Unstar' : '☆ Shortlist'}
          </button>
          {item.stage === 'snoozed'
            ? <button className="ghost" onClick={() => patch({ snoozed_until: null })}>Back to queue</button>
            : <button className="ghost" onClick={snoozeDay} title="hide for 24 hours">Later</button>}
          <button className="ghost" onClick={() => setEditingNote(true)}>{item.note ? 'Edit note' : 'Note'}</button>
          {item.stage === 'dismissed'
            ? <button className="ghost" onClick={() => patch({ status: item.draft ? 'drafted' : 'new' })}>Restore</button>
            : <button className="ghost" onClick={() => patch({ status: 'dismissed' })}>Dismiss</button>}
        </>}
      </div>
      {open && alert && (
        <div className="embedded">
          <AlertCard alert={alert} busy={busy}
            onRegenerate={async (angle) => onJob(await api.regenerate(alert.id, angle))}
            onStatus={async (s) => { await api.setAlertStatus(alert.id, s); onChange() }}
            onDraftChange={onChange} />
        </div>
      )}
    </section>
  )
}
