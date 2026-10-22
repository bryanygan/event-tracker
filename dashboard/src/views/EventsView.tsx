import { useEffect, useState } from 'react'
import { api, type EventRow } from '../api'

export default function EventsView({ refreshKey }: { refreshKey: number }) {
  const [q, setQ] = useState('')
  const [genre, setGenre] = useState('')
  const [days, setDays] = useState(30)
  const [mine, setMine] = useState(false)
  const [genres, setGenres] = useState<string[]>([])
  const [events, setEvents] = useState<EventRow[] | null>(null)

  useEffect(() => { api.genres().then(setGenres) }, [refreshKey])
  useEffect(() => {
    const t = setTimeout(() => api.events({ q, genre, days }).then(setEvents), 200) // debounce typing
    return () => clearTimeout(t)
  }, [q, genre, days, refreshKey])

  const newCutoff = Date.now() - 3 * 24 * 3600 * 1000
  const shown = (events ?? []).filter((e) => !mine || e.my_plays > 0)
  return (
    <>
      <div className="toolbar">
        <input placeholder="Search artist or venue" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={genre} onChange={(e) => setGenre(e.target.value)}>
          <option value="">All genres</option>
          {genres.map((g) => <option key={g}>{g}</option>)}
        </select>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 30, 60, 120, 240].map((d) => <option key={d} value={d}>Next {d} days</option>)}
        </select>
        <label className="small"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> 🎧 artists I listen to</label>
        <span className="muted small">{shown.length} events</span>
      </div>
      <div className="table-wrap">
        <table className="events">
          <thead>
            <tr><th>Date</th><th>Artist</th><th>Venue</th><th>Genre</th><th>Price</th><th title="Deezer fans">Fans</th><th title="your plays, last 2 years">🎧</th><th title="notability score">★</th><th>Status</th></tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <tr key={e.id}>
                <td className="nowrap">{fmtDate(e.local_date)}{e.local_time ? ` ${fmtTime(e.local_time)}` : ''}</td>
                <td>
                  {e.url ? <a href={e.url} target="_blank" rel="noreferrer">{e.headliner}</a> : e.headliner}
                  {new Date(e.first_seen).getTime() > newCutoff && <span className="pill new">new</span>}
                  {e.support && <div className="muted small">w/ {e.support}</div>}
                </td>
                <td>{e.venue_name}</td>
                <td>{e.genre}{e.subgenre ? <span className="muted small"> · {e.subgenre}</span> : null}</td>
                <td className="nowrap">{e.price_min != null ? `$${e.price_min}${e.price_max && e.price_max !== e.price_min ? `–${e.price_max}` : ''}` : ''}</td>
                <td className="nowrap">{e.fans != null ? fmtFans(e.fans) : ''}</td>
                <td className="nowrap" title={e.niche ? 'in your genre lane' : ''}>{e.my_plays ? e.my_plays : e.niche ? '·' : ''}</td>
                <td>{Math.round(e.score)}</td>
                <td>{e.status !== 'onsale' ? <span className="pill">{e.status}</span> : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

const fmtFans = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n)
const fmtDate = (d: string) =>
  new Date(d + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
const fmtTime = (t: string) => {
  const [h, m] = t.split(':').map(Number)
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')}${h < 12 ? 'a' : 'p'}`
}
