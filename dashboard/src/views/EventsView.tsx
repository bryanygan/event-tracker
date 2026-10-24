import { useEffect, useState } from 'react'
import { api, type EventKind, type EventRow } from '../api'
import ManualEvents from './ManualEvents'

const KINDS: [EventKind, string][] = [['', 'All kinds'], ['music', 'Concerts'], ['sports', 'Games'],
  ['comedy', 'Comedy'], ['local', 'Markets, festivals & more']]
// Where a non-Ticketmaster listing came from (provenance, P2-14)
const SOURCE_LABEL: Record<string, string> = { ccd: 'Center City District', drwc: 'Delaware River Waterfront',
  manual: 'added by you' }

// Where a genre came from (tracker/genres.py); shown as a tooltip, with ≈ for name-matched guesses
const GENRE_SOURCE: Record<string, string> = {
  listing: 'genre from the listing',
  artist: "Ticketmaster's genre for the artist (the listing had none)",
  tm_search: 'inferred: this venue-calendar act matched a Ticketmaster artist by exact name',
  deezer: "inferred: the most common genre of the artist's releases on Deezer",
}

function genreTitle(e: { genre_source?: string | null; ticketmaster_genre?: string | null }): string {
  const base = GENRE_SOURCE[e.genre_source ?? ''] ?? ''
  return e.ticketmaster_genre ? `${base} (Ticketmaster says ${e.ticketmaster_genre})` : base
}

export default function EventsView({ refreshKey }: { refreshKey: number }) {
  const [q, setQ] = useState('')
  const [genre, setGenre] = useState('')
  const [kind, setKind] = useState<EventKind>('')
  const [added, setAdded] = useState(0)  // bumped when you add/edit one of your events
  const [days, setDays] = useState(30)
  const [mine, setMine] = useState(false)
  const [genres, setGenres] = useState<string[]>([])
  const [events, setEvents] = useState<EventRow[] | null>(null)

  useEffect(() => { api.genres().then(setGenres) }, [refreshKey])
  useEffect(() => {
    const t = setTimeout(() => api.events({ q, genre, kind, days }).then(setEvents), 200) // debounce typing
    return () => clearTimeout(t)
  }, [q, genre, kind, days, refreshKey, added])

  const newCutoff = Date.now() - 3 * 24 * 3600 * 1000
  const shown = (events ?? []).filter((e) => !mine || e.my_plays > 0)
  return (
    <>
      <ManualEvents onChange={() => setAdded((n) => n + 1)} />
      <div className="toolbar">
        <input placeholder="Search artist or venue" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={kind} onChange={(e) => setKind(e.target.value as EventKind)}>
          {KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
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
                <td className="nowrap">{fmtDate(e.local_date)}{e.end_date ? ` – ${fmtDate(e.end_date)}` : ''}{e.local_time ? ` ${fmtTime(e.local_time)}` : ''}</td>
                <td>
                  {e.url ? <a href={e.url} target="_blank" rel="noreferrer">{e.headliner}</a> : e.headliner}
                  {new Date(e.first_seen).getTime() > newCutoff && <span className="pill new">new</span>}
                  {e.support && <div className="muted small">w/ {e.support}</div>}
                  {e.source && SOURCE_LABEL[e.source] && <div className="muted small">listed by {SOURCE_LABEL[e.source]}</div>}
                </td>
                <td>{e.venue_name}</td>
                <td title={genreTitle(e)}>
                  {(e.genre_source === 'tm_search' || e.genre_source === 'deezer') && e.genre ? <span className="muted">≈ </span> : null}
                  {e.genre}{e.subgenre ? <span className="muted small"> · {e.subgenre}</span> : null}
                </td>
                <td className="nowrap">{e.price_min === 0 && !e.price_max ? (e.segment === 'Experience' ? 'Free' : '') : e.price_min != null ? `$${e.price_min}${e.price_max && e.price_max !== e.price_min ? `–${e.price_max}` : ''}` : ''}</td>
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
