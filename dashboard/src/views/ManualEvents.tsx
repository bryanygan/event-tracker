import { useEffect, useState } from 'react'
import { api, STATIC, type ManualEvent, type ManualEventInput } from '../api'

// Events you add yourself (tracker/manual_events.py): food fests, pop-ups and markets that live on Eventbrite or
// Instagram, which the tracker can't read. Every fact here is yours; saving one raises its idea right away.
const EMPTY: ManualEventInput = { name: '', category: 'Food & Drink', start_date: '', end_date: '', start_time: '',
  venue: '', city: 'Philadelphia', url: '', notes: '', free: false }

export default function ManualEvents({ onChange }: { onChange: () => void }) {
  const [events, setEvents] = useState<ManualEvent[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [form, setForm] = useState<ManualEventInput>(EMPTY)
  const [price, setPrice] = useState({ min: '', max: '' })
  const [editing, setEditing] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)

  const load = () => api.manualEvents().then((r) => { setEvents(r.events); setCategories(r.categories) })
  useEffect(() => { load() }, [])
  if (STATIC) return null

  const set = (k: keyof ManualEventInput) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })
  const startEdit = (m: ManualEvent) => {
    setEditing(m.id); setOpen(true); setError('')
    const free = m.price_min === 0 && !m.price_max
    setForm({ ...m, end_date: m.end_date ?? '', start_time: m.start_time ?? '', city: m.city ?? '', url: m.url ?? '',
      notes: m.notes ?? '', free })
    setPrice({ min: free || m.price_min == null ? '' : String(m.price_min), max: m.price_max && m.price_max !== m.price_min ? String(m.price_max) : '' })
  }
  const reset = () => { setForm(EMPTY); setPrice({ min: '', max: '' }); setEditing(null); setError('') }
  const save = async () => {
    const body: ManualEventInput = { ...form,
      price_min: form.free || price.min === '' ? null : Number(price.min),
      price_max: form.free || price.max === '' ? null : Number(price.max) }
    try {
      if (editing) await api.updateManualEvent(editing, body)
      else await api.createManualEvent(body)
      reset(); setOpen(false); await load(); onChange()
    } catch (e) { setError(String(e).replace(/^Error: /, '')) }
  }
  const remove = async (m: ManualEvent) => {
    if (!confirm(`Remove "${m.name}"? Its idea is dismissed unless you've posted it.`)) return
    await api.deleteManualEvent(m.id); await load(); onChange()
  }

  return (
    <details className="card manual-events" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary><strong>Your events</strong> <span className="muted small">({events.length}) · add a food fest, pop-up or market
        from a link: you type the facts, nothing is looked up</span></summary>
      {events.length > 0 && (
        <table className="events">
          <tbody>
            {events.map((m) => (
              <tr key={m.id}>
                <td className="nowrap">{m.start_date}{m.end_date ? ` – ${m.end_date}` : ''}{m.start_time ? ` ${m.start_time}` : ''}</td>
                <td>{m.url ? <a href={m.url} target="_blank" rel="noreferrer">{m.name}</a> : m.name}
                  <span className="muted small"> · {m.category}</span></td>
                <td>{m.venue}</td>
                <td className="nowrap">{m.price_min === 0 && !m.price_max ? 'Free' : m.price_min != null ? `$${m.price_min}${m.price_max && m.price_max !== m.price_min ? `–${m.price_max}` : ''}` : ''}</td>
                <td className="nowrap"><button className="link" onClick={() => startEdit(m)}>Edit</button>{' '}
                  <button className="link" onClick={() => remove(m)}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="manual-form">
        <h3>{editing ? 'Edit event' : 'Add an event'}</h3>
        <label>Name <input value={form.name ?? ''} onChange={set('name')} placeholder="Philly Night Market: Chinatown" /></label>
        <label>Category <select value={form.category ?? ''} onChange={set('category')}>
          {categories.map((c) => <option key={c}>{c}</option>)}</select></label>
        <label>Starts <input type="date" value={form.start_date ?? ''} onChange={set('start_date')} /></label>
        <label>Ends <span className="muted small">(multi-day)</span> <input type="date" value={form.end_date ?? ''} onChange={set('end_date')} /></label>
        <label>Time <input type="time" value={form.start_time ?? ''} onChange={set('start_time')} /></label>
        <label>Where <input value={form.venue ?? ''} onChange={set('venue')} placeholder="venue or place" /></label>
        <label>City <input value={form.city ?? ''} onChange={set('city')} /></label>
        <label className="inline"><input type="checkbox" checked={!!form.free} onChange={(e) => setForm({ ...form, free: e.target.checked })} />
          Free <span className="muted small">(only if the listing says so)</span></label>
        {!form.free && <>
          <label>Price from <input type="number" min="0" step="0.01" value={price.min} onChange={(e) => setPrice({ ...price, min: e.target.value })} placeholder="blank = not stated" /></label>
          <label>to <input type="number" min="0" step="0.01" value={price.max} onChange={(e) => setPrice({ ...price, max: e.target.value })} /></label>
        </>}
        <label className="wide">Link <input value={form.url ?? ''} onChange={set('url')} placeholder="https://… (where you saw it)" /></label>
        <label className="wide">Notes <span className="muted small">(for you; drafts never see them)</span>
          <input value={form.notes ?? ''} onChange={set('notes')} /></label>
        {error && <p className="error-text">{error}</p>}
        <div>
          <button onClick={save}>{editing ? 'Save changes' : 'Add event'}</button>{' '}
          {editing && <button className="link" onClick={reset}>Cancel</button>}
        </div>
      </div>
    </details>
  )
}
