import { useEffect, useState } from 'react'
import { api, STATIC, type Alert, type Draft, type Job, type VinylOverview, type VinylRecord } from '../api'
import { fmtWhen } from '../App'
import { AlertCard } from './AlertsView'

// tracker/vinyl.py's formats
export const VINYL_LABELS: Record<string, string> = {
  vinyl_unique: 'Most unique pressings', vinyl_value: 'Most valuable', vinyl_coveted: 'Most wanted',
  vinyl_variants: 'Same album, different pressings', vinyl_haul: 'New pickups', vinyl_numbers: 'By the numbers',
  vinyl_look_into: 'Worth looking into', vinyl_underrated: 'Underrated of the day',
  vinyl_underappreciated: 'Underappreciated album', vinyl_lore: 'The story behind it', vinyl_anniversary: 'Anniversary',
}
// Researched one-record stories: these get "Dig deeper"
export const VINYL_STORY_FORMATS = ['vinyl_lore', 'vinyl_underappreciated', 'vinyl_underrated', 'vinyl_anniversary']
const MAKE: [string, string][] = [['vinyl_lore', 'The story behind it'], ['vinyl_underappreciated', 'Underappreciated album'],
  ['vinyl_underrated', 'Underrated of the day']]

type SortKey = 'added' | 'value' | 'wanted' | 'unique' | 'rarest' | 'rated' | 'oldest'
const SORTS: [SortKey, string][] = [['added', 'Recently added'], ['value', 'Most valuable'], ['wanted', 'Most wanted'],
  ['unique', 'Most unique pressing'], ['rarest', 'Fewest owners'], ['rated', 'Highest rated'], ['oldest', 'Oldest album']]
const SORT_FN: Record<SortKey, (a: VinylRecord, b: VinylRecord) => number> = {
  added: (a, b) => (b.date_added ?? '').localeCompare(a.date_added ?? ''),
  value: (a, b) => (b.suggested_price ?? -1) - (a.suggested_price ?? -1),
  wanted: (a, b) => (b.want ?? -1) - (a.want ?? -1),
  unique: (a, b) => (b.unique_score ?? -1) - (a.unique_score ?? -1),
  rarest: (a, b) => (a.have ?? 1e9) - (b.have ?? 1e9),
  rated: (a, b) => (b.rating_avg ?? -1) - (a.rating_avg ?? -1),
  oldest: (a, b) => (a.album_first_released ?? '9999').localeCompare(b.album_first_released ?? '9999'),
}

type Props = { refreshKey: number; busy: boolean; job: Job | null; onJob: (j: Job) => void; onChange: () => void }

export default function VinylView({ refreshKey, busy, job, onJob, onChange }: Props) {
  const [data, setData] = useState<VinylOverview | null>(null)
  const [ideas, setIdeas] = useState<Alert[] | null>(null)
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('added')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    api.vinyl().then(setData).catch((e) => setError(String(e)))
    api.alerts('open', 'vinyl').then(setIdeas).catch((e) => setError(String(e)))
  }
  useEffect(() => { load() }, [refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const sync = async () => {
    setError(null)
    try { onJob(await api.vinylSync()); setMessage('Reading your collection from Discogs. The first sync looks up every record and takes a few minutes.') }
    catch (e) { setError(String(e)) }
  }
  const makeIdea = async (r: VinylRecord, format: string) => {
    setError(null)
    try {
      await api.vinylIdea(r.instance_id, format)
      setMessage(`Added “${VINYL_LABELS[format]}” for ${r.artist} – ${r.title} to the ideas above.`)
      load(); onChange()
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) { setError(String(e)) }
  }

  if (!data || !ideas) return <p className="muted">{error ?? 'Loading…'}</p>
  if (!data.enabled && !STATIC) return <p className="muted">Add DISCOGS_TOKEN to <code>.env</code> to read your collection.</p>
  const ql = q.trim().toLowerCase()
  const shown = data.records.filter((r) => !ql || `${r.artist} ${r.title} ${r.pressing} ${r.styles.join(' ')}`.toLowerCase().includes(ql))
    .sort(SORT_FN[sort])
  const ordered = [...ideas].sort((a, b) => (a.status === 'new' ? 0 : 1) - (b.status === 'new' ? 0 : 1) || b.score - a.score)

  return (
    <>
      <section className="card vinyl-summary">
        <div>
          <strong>{data.records.length} records</strong>
          {data.value && <span title="Discogs' estimate from recent sales (low · median · high)">
            {' · '}worth about {data.value.median} on Discogs ({data.value.minimum}–{data.value.maximum})</span>}
          <div className="muted small">
            {data.synced_at ? `Synced from Discogs ${fmtWhen(data.synced_at)}` : 'Not synced yet'}
            {data.pending_lookups > 0 && ` · ${data.pending_lookups} records still to look up (prices, owners), a batch each run`}
            {' · '}the 6:30 and 4:30 runs keep it current
          </div>
        </div>
        {!STATIC && <button className="ghost" onClick={sync} disabled={busy}>Sync from Discogs</button>}
      </section>
      {message && <div className="draft-done" role="status">{message}</div>}
      {error && <div className="warn" role="status">{error}</div>}

      <h2 className="section-title">Video ideas ({ordered.length})</h2>
      <p className="muted small">Lists come straight from your Discogs data. Stories are researched when you click
        <em> Research &amp; write draft</em>: written only from Discogs, MusicBrainz and Wikipedia, with every claim
        checked against them (a few cents each). <em>Dig deeper</em> adds a web search for interviews and articles.</p>
      {ordered.length === 0 && <p className="muted">No vinyl ideas yet: they appear after a sync, or make one from a record below.</p>}
      {ordered.map((a) => (
        <AlertCard key={a.id} alert={a} busy={busy} job={job}
          onRegenerate={async (angle, notes) => onJob(await api.regenerate(a.id, angle, notes))}
          onStatus={async (s) => { await api.setAlertStatus(a.id, s); load(); onChange() }}
          onDraftChange={() => { load(); onChange() }} />
      ))}

      <h2 className="section-title">Your collection</h2>
      <div className="toolbar">
        <input type="search" placeholder="Search artist, album, pressing or style" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
          {SORTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
        <span className="muted small">{shown.length} records</span>
      </div>
      <div className="table-wrap">
        <table className="events vinyl">
          <thead>
            <tr><th></th><th>Record</th><th title="Discogs' suggested price from recent sales">Value</th>
              <th title="Discogs users who have / want this pressing">Have · want</th><th title="Discogs rating">★</th>
              <th title="how unusual the pressing is: hover for why">Unique</th>{!STATIC && <th>Make an idea</th>}</tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.instance_id}>
                <td>{r.cover_url && <img className="cover" src={r.cover_url} alt="" loading="lazy" />}</td>
                <td>
                  <a href={r.url} target="_blank" rel="noreferrer">{r.artist} – {r.title}</a>
                  <div className="muted small">{r.pressing}{r.year ? ` · ${r.year} pressing` : ''}
                    {r.album_first_released ? ` · album ${r.album_first_released.slice(0, 4)}` : ''}</div>
                </td>
                <td className="nowrap" title={r.price_condition ?? ''}>{r.suggested_price != null ? `$${Math.round(r.suggested_price)}` : ''}</td>
                <td className="nowrap">{r.have != null ? `${fmtCount(r.have)} · ${fmtCount(r.want ?? 0)}` : <span className="muted">…</span>}</td>
                <td className="nowrap">{r.rating_avg ? r.rating_avg.toFixed(1) : ''}</td>
                <td title={r.unique_why.join(', ')}>{r.unique_score ?? ''}</td>
                {!STATIC && <td>
                  <select value="" onChange={(e) => e.target.value && makeIdea(r, e.target.value)} aria-label="Make a video idea from this record">
                    <option value="">Make idea…</option>
                    {MAKE.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                  </select>
                </td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

const fmtCount = (n: number) => (n >= 1e4 ? `${Math.round(n / 1e3)}K` : n.toLocaleString())
const money = (n: unknown) => (typeof n === 'number' ? `$${Math.round(n)}` : '')

type RecordFacts = {
  artist: string; title: string; pressing?: string; cover_url?: string; discogs_url?: string; suggested_price_usd?: number
  price_condition?: string; discogs_have?: number; discogs_want?: number; limited_to?: number; what_makes_it_unusual?: string[]
  album_first_released?: string; discogs_rating?: number; added?: string; first_released_year?: number
}

/** A vinyl idea's facts: its records as a table (the only things the draft may say, besides its sources) */
export function VinylFacts({ facts, open }: { facts: Alert['facts']; open: boolean }) {
  const recs = (facts.record ? [facts.record] : facts.records ?? []) as RecordFacts[]
  const why = (facts.why as string[] | undefined) ?? []
  const ann = facts.anniversary as { years: number; date: string; first_released: string } | undefined
  const rest = Object.fromEntries(Object.entries(facts).filter(([k]) => !['record', 'records', 'why', 'format', 'anniversary'].includes(k)))
  return (
    <details className="facts" open={open}>
      <summary>{VINYL_LABELS[String(facts.format)] ?? 'Vinyl'}
        {recs.length > 0 && ` · ${recs.length} record${recs.length === 1 ? '' : 's'}`} (from Discogs)</summary>
      {why.length > 0 && <p className="muted small">{why.join(' · ')}</p>}
      {ann && <p className="small"><strong>{ann.years} years</strong> on {ann.date} (first released {ann.first_released}, MusicBrainz)</p>}
      <table className="vinyl">
        <tbody>
          {recs.map((r, i) => (
            <tr key={i}>
              <td>{r.cover_url && <img className="cover" src={r.cover_url} alt="" loading="lazy" />}</td>
              <td>
                {r.discogs_url ? <a href={r.discogs_url} target="_blank" rel="noreferrer">{r.artist} – {r.title}</a> : <>{r.artist} – {r.title}</>}
                <div className="muted small">{[r.pressing, r.album_first_released && `album ${r.album_first_released}`,
                  r.first_released_year && `first released ${r.first_released_year}`, r.added && `added ${r.added}`].filter(Boolean).join(' · ')}</div>
                {r.what_makes_it_unusual && <div className="small">{r.what_makes_it_unusual.join(', ')}</div>}
              </td>
              <td className="nowrap" title={r.price_condition ?? ''}>{money(r.suggested_price_usd)}</td>
              <td className="nowrap">{r.discogs_have != null ? `${fmtCount(r.discogs_have)} have · ${fmtCount(r.discogs_want ?? 0)} want` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.keys(rest).length > 0 && (
        <ul className="vinyl-numbers small">
          {Object.entries(rest).map(([k, v]) => <li key={k}><span className="muted">{k.replace(/_/g, ' ')}:</span> {readable(v)}</li>)}
        </ul>
      )}
    </details>
  )
}

/** A facts value as one line: lists of {artist, records} pairs, a record, or a price range */
function readable(v: unknown): string {
  if (Array.isArray(v)) return v.map(readable).join(' · ')
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    if ('artist' in o && 'title' in o) return `${o.artist} – ${o.title}${o.album_first_released ? ` (${o.album_first_released})` : ''}`
    if ('median' in o) return `${o.median} median (${o.minimum}–${o.maximum})`
    return Object.values(o).map(readable).join(' ')
  }
  return String(v)
}

const VERDICT: Record<string, [string, string]> = { supported: ['✓', 'backed by the sources'], partly: ['~', 'partly backed'],
  unsupported: ['✗', 'not in the sources'] }

/** What a vinyl draft was written from, and how each of its claims checked out */
export function VinylSources({ record }: { record: NonNullable<Draft['sources']> }) {
  const bad = record.claims.filter((c) => c.verdict !== 'supported').length
  return (
    <details className="vinyl-sources">
      <summary>Sources ({record.sources.length}{record.deep ? ', with web search' : ''}) · {record.claims.length} claims checked
        {bad ? `, ${bad} to look at` : ', all backed'}</summary>
      <ol className="small">
        {record.sources.map((s) => (
          <li key={s.n} value={s.n}>
            {s.url.startsWith('http') ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}
            <span className="muted"> · {s.kind.replace('_', ' ')}</span>
          </li>
        ))}
      </ol>
      <ul className="claims small">
        {record.claims.map((c, i) => (
          <li key={i} className={`claim ${c.verdict}`} title={VERDICT[c.verdict]?.[1]}>
            <span className="verdict">{VERDICT[c.verdict]?.[0] ?? '?'}</span> {c.text}
            <span className="muted"> {c.sources.map((n) => (n === 0 ? 'facts' : `S${n}`)).join(', ')}
              {c.verdict !== 'supported' && c.note ? ` · ${c.note}` : ''}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}
