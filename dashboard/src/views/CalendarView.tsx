import { useEffect, useState } from 'react'
import { api, STATIC, type Calendar, type CalendarDay, type CalendarItem } from '../api'
import { fmtWhen } from '../App'
import { KIND_LABEL } from './AlertsView'

// Editorial calendar (tracker/planner.py): plans are yours; nothing posts at the planned time.

// <input type="datetime-local"> works in local time without a zone; the API wants UTC with one.
const toLocalInput = (d: Date) => new Date(+d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

/** The time "plan on this day" picks: the default hour, or 2 hours before a post-by that comes earlier. */
function dayDefault(dateIso: string, hour: number, postBy?: string | null): Date {
  const [y, m, d] = dateIso.split('-').map(Number)
  const at = new Date(y, m - 1, d, hour)
  if (postBy) {
    const early = new Date(+new Date(postBy) - 2 * 36e5)
    if (early < at && early.toDateString() === at.toDateString()) return early
  }
  return at
}

/** 2 hours before a post-by that's still ahead, else undefined (PlanControl then picks tomorrow 6 PM). */
function beforeDeadline(postBy: string | null): Date | undefined {
  if (!postBy) return undefined
  const at = new Date(+new Date(postBy) - 2 * 36e5)
  return at > new Date() ? at : undefined
}

function shiftDays(iso: string, n: number) {
  const [y, m, d] = iso.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + n))
  return dt.toISOString().slice(0, 10)
}

export function PlanControl({ alertId, plannedFor, series, seriesSet, knownSeries = [], defaultAt, onDone, onCancel }: {
  alertId: number; plannedFor: string | null; series: string | null; seriesSet?: string | null
  knownSeries?: string[]; defaultAt?: Date; onDone: () => void; onCancel: () => void
}) {
  const [when, setWhen] = useState(() => {
    if (plannedFor) return toLocalInput(new Date(plannedFor))
    if (defaultAt) return toLocalInput(defaultAt)
    const t = new Date(Date.now() + 864e5) // tomorrow 6 PM
    t.setHours(18, 0, 0, 0)
    return toLocalInput(t)
  })
  const [name, setName] = useState(seriesSet ?? '')
  const [error, setError] = useState<string | null>(null)
  const save = async (fields: { planned_for: string | null; series?: string | null }) => {
    setError(null)
    try {
      await api.updateAlert(alertId, fields)
      onDone()
    } catch (e) {
      setError(String(e))
    }
  }
  const listId = `series-${alertId}`
  return (
    <div className="post-form plan-form">
      <div className="form-grid">
        <label>Plan to post<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
        <label>Series
          <input list={listId} value={name} placeholder={series ?? 'none'} onChange={(e) => setName(e.target.value)} />
          <datalist id={listId}>{knownSeries.map((s) => <option key={s} value={s} />)}</datalist>
        </label>
      </div>
      <span className="muted small">Just a plan for you: nothing posts at this time. Leave the series blank for the default ({series ?? 'none'}).</span>
      <div className="actions-inline">
        <button disabled={!when} onClick={() => save({ planned_for: new Date(when).toISOString(), series: name.trim() || null })}>
          {plannedFor ? 'Move' : 'Plan'}
        </button>
        {plannedFor && <button className="ghost" onClick={() => save({ planned_for: null })}>Take off calendar</button>}
        <button className="ghost" onClick={onCancel}>Cancel</button>
      </div>
      {error && <p className="error-text small">{error}</p>}
    </div>
  )
}

export default function CalendarView({ refreshKey, onChange }: { refreshKey: number; onChange: () => void }) {
  const [start, setStart] = useState<string | undefined>(undefined)
  const [cal, setCal] = useState<Calendar | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () => api.calendar(start).then(setCal).catch((e) => setError(String(e)))
  useEffect(() => { load() }, [refreshKey, start]) // eslint-disable-line react-hooks/exhaustive-deps
  const changed = () => { load(); onChange() }

  if (error) return <p className="error-text">{error}</p>
  if (!cal) return <p className="muted">Loading…</p>
  const known = cal.series.map((s) => s.name)
  const weeks = [cal.days.slice(0, 7), cal.days.slice(7, 14)].filter((w) => w.length)

  return (
    <>
      <div className="toolbar">
        <button className="ghost" onClick={() => setStart(shiftDays(cal.start, -7))}>← Earlier</button>
        <button className="ghost" onClick={() => setStart(undefined)}>This week</button>
        <button className="ghost" onClick={() => setStart(shiftDays(cal.start, 7))}>Later →</button>
        <span className="muted small">A loose plan: put as many ideas on a day as you like and pick on the day. Nothing posts at a planned time.</span>
      </div>

      {weeks.map((w, n) => (
        <div key={n} className="cal-week">
          {w.map((d) => <Day key={d.date} day={d} cal={cal} known={known} onChange={changed} />)}
        </div>
      ))}

      <h3 className="section-title cal-h">Not on the calendar yet</h3>
      {cal.unplanned.length === 0 && <p className="muted">Every active idea is planned.</p>}
      {cal.unplanned.map((i) => <Unplanned key={i.alert_id} item={i} known={known} onChange={changed} />)}

      <h3 className="section-title cal-h">Series</h3>
      <div className="table-wrap">
        <table className="events">
          <thead><tr><th>Series</th><th>Last posted</th><th>Next planned</th><th>Ideas waiting</th><th>Usually</th></tr></thead>
          <tbody>
            {cal.series.map((s) => (
              <tr key={s.name}>
                <td>{s.name}{s.behind && <span className="pill" title="last post is older than usual">behind</span>}</td>
                <td>{s.last_posted ? fmtWhen(s.last_posted) : <span className="muted">never</span>}</td>
                <td>{s.next_planned ? fmtWhen(s.next_planned) : <span className="muted">–</span>}</td>
                <td>{s.ideas}</td>
                <td className="muted">{s.cadence_days ? `every ${s.cadence_days} days` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {cal.mix.length > 0 && <>
        <h3 className="section-title cal-h">Mix</h3>
        {cal.mix.map((b) => (
          <p key={b.week} className="small">
            <strong>{b.label}</strong>: {Object.entries(b.formats).map(([f, n]) => `${n} ${f.replace('_', ' ')}`).join(', ')}
            {b.music > 0 && <span className="muted"> · {b.lane} of {b.music} music videos in your lane</span>}
          </p>
        ))}
      </>}
    </>
  )
}

function Day({ day, cal, known, onChange }: { day: CalendarDay; cal: Calendar; known: string[]; onChange: () => void }) {
  const [editing, setEditing] = useState<number | null>(null)
  const [adding, setAdding] = useState('')
  const empty = !day.planned.length && !day.posted.length && !day.due.length
  const planHere = async (alertId: number, postBy?: string | null) => {
    await api.updateAlert(alertId, { planned_for: dayDefault(day.date, cal.default_hour, postBy).toISOString() })
    setAdding('')
    onChange()
  }
  return (
    <section className={`cal-day${day.today ? ' today' : ''}${day.past ? ' past' : ''}`}>
      <div className="cal-date">{day.label}{day.today && <span className="pill new">today</span>}</div>
      {day.posted.map((p) => (
        <div key={`p${p.id}`} className="cal-entry posted" title={p.series ?? ''}>
          ✓ {fmtTime(p.posted_at)} {p.url ? <a href={p.url} target="_blank" rel="noreferrer">{p.title}</a> : p.title}
        </div>
      ))}
      {day.planned.map((i) => (
        <div key={`a${i.alert_id}`} className={`cal-entry planned kind-${i.kind}`}>
          <button className="link" disabled={STATIC} onClick={() => setEditing(editing === i.alert_id ? null : i.alert_id)}
                  title={i.series ?? ''}>
            {fmtTime(i.planned_for!)} · {i.title}
          </button>
          {i.series && <div className="muted small">{i.series}</div>}
          {i.late && <div className="muted small">{i.late}</div>}
          {editing === i.alert_id && (
            <PlanControl alertId={i.alert_id} plannedFor={i.planned_for} series={i.series} seriesSet={i.series_set} knownSeries={known}
                         onDone={() => { setEditing(null); onChange() }} onCancel={() => setEditing(null)} />
          )}
        </div>
      ))}
      {day.due.map((i) => (
        <div key={`d${i.alert_id}`} className="cal-entry due">
          <span className="small">⏰ due: {i.title}</span>
          {!STATIC && <button className="copy" onClick={() => planHere(i.alert_id, i.post_by)}>Plan this day</button>}
        </div>
      ))}
      {empty && <div className="muted small">{day.past ? '–' : 'Nothing planned'}</div>}
      {!STATIC && !day.past && cal.unplanned.length > 0 && (
        <select className="cal-add" value={adding} onChange={(e) => {
          setAdding(e.target.value)
          const item = cal.unplanned.find((u) => String(u.alert_id) === e.target.value)
          if (item) planHere(item.alert_id, item.post_by)
        }}>
          <option value="">+ add an idea</option>
          {cal.unplanned.map((u) => <option key={u.alert_id} value={u.alert_id}>{u.title}</option>)}
        </select>
      )}
    </section>
  )
}

function Unplanned({ item, known, onChange }: { item: CalendarItem; known: string[]; onChange: () => void }) {
  const [planning, setPlanning] = useState(false)
  return (
    <div className={`card cal-unplanned kind-${item.kind}`}>
      <div className="card-head">
        <span className="pill">{KIND_LABEL[item.kind as keyof typeof KIND_LABEL] ?? item.kind}</span>
        {item.series && <span className="pill">{item.series}</span>}
        {item.post_by && <span className="pill due">Post by {fmtWhen(item.post_by)}</span>}
        <h2>{item.title}</h2>
        {!STATIC && !planning && <button className="ghost" onClick={() => setPlanning(true)}>📅 Plan</button>}
      </div>
      {planning && (
        <PlanControl alertId={item.alert_id} plannedFor={null} series={item.series} seriesSet={item.series_set}
                     knownSeries={known} defaultAt={beforeDeadline(item.post_by)}
                     onDone={() => { setPlanning(false); onChange() }} onCancel={() => setPlanning(false)} />
      )}
    </div>
  )
}
