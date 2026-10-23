import { useEffect, useMemo, useState } from 'react'
import { api, type Story, type StoryArtist } from '../api'
import { fmtWhen } from '../App'

// Watchlist stories (tracker/stories.py, roadmap M3): articles about the same thing grouped into one card, with
// the rule that typed it and every source. Scoring and "Send to queue" come in M5.
const TYPE_LABEL: Record<string, string> = {
  album_announcement: 'Album announced', album_release: 'Album out', single_release: 'New song / video',
  tracklist: 'Tracklist', tease: 'Tease', collaboration: 'Collab', tour: 'Tour', chart: 'Chart',
  brand_collab: 'Brand / media', legal: 'Legal', sensitive: 'Death / health', politics: 'Politics',
  lifestyle: 'Lifestyle', anniversary: 'Anniversary', review: 'Review', roundup: 'Roundup',
  artist_development: 'Career news', other: 'Other',
}
const VERIFY: Record<Story['verification'], string> = {
  unverified: 'one source so far',
  corroborated: 'two or more outlets report it',
  confirmed: 'matches a catalog or official source',
  conflicting: 'sources disagree (see the evidence)',
}

export default function StoriesList({ refreshKey, days, origin }: { refreshKey: number; days: number; origin: Story['origin'] }) {
  const [stories, setStories] = useState<Story[] | null>(null)
  const [q, setQ] = useState('')
  const [type, setType] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.stories(days, origin).then(setStories).catch((e) => setError(String(e))) }, [days, origin, refreshKey])

  const types = useMemo(() => [...new Set((stories ?? []).map((s) => s.story_type))].sort(), [stories])
  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return (stories ?? []).filter((s) => (!type || s.story_type === type) &&
      (!ql || s.title.toLowerCase().includes(ql) || [s.primary_artist, ...s.other_artists]
        .some((a) => a?.name.toLowerCase().includes(ql))))
  }, [stories, q, type])

  return (
    <>
      <div className="toolbar">
        <input placeholder="Search stories or artists" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All kinds</option>
          {types.map((t) => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}
        </select>
        <span className="muted small">{stories ? `${shown.length} stories` : 'Loading…'}</span>
      </div>
      {error && <div className="banner error">{error}</div>}
      {stories && shown.length === 0 && <p className="muted">No stories about your watchlist artists in this window yet.</p>}
      {shown.map((s) => (
        <div key={s.id} className="card">
          <div className="card-head">
            <div>
              <div className="trend-pills">
                <span className="pill" title={s.rule ?? ''}>{TYPE_LABEL[s.story_type] ?? s.story_type}</span>
                {s.release_status && <span className="pill">{s.release_status}</span>}
                <span className={`pill${s.verification === 'conflicting' ? ' pill-warn' : ''}`} title={VERIFY[s.verification]}>
                  {s.origin === 'catalog' ? 'catalog' : `${s.source_count} source${s.source_count === 1 ? '' : 's'}`}
                  {s.verification === 'conflicting' ? ' · conflicting' : s.verification === 'confirmed' ? ' · confirmed' : ''}
                </span>
                {s.sensitive && <span className="pill pill-warn" title="Deaths, arrests, lawsuits, health: handle with care; never promoted automatically">sensitive</span>}
              </div>
              <strong>{s.title}</strong>
              <div className="small">
                {[s.primary_artist, ...s.other_artists].filter(Boolean).map((a) => <ArtistName key={a!.id} a={a!} />)}
                {s.release_title && <span className="muted"> · “{s.release_title}”</span>}
                {s.release_date && <span className="muted"> · release date {s.release_date}
                  {s.evidence.some((e) => e.kind === 'catalog') ? ' (per the catalogs)' : ' (as reported)'}</span>}
              </div>
            </div>
            <span className="muted small nowrap" style={{ marginLeft: 'auto' }} title={s.dated ? '' : 'the feed gives no date; it was already there when tracking began'}>
              {s.dated ? fmtWhen(s.last_seen) : 'before tracking'}
            </span>
          </div>
          <button className="ghost small" onClick={() => setOpen(open === s.id ? null : s.id)}>
            {open === s.id ? 'Hide sources' : s.origin === 'catalog' ? 'Where it’s listed' : `Sources and why (${s.sources.length})`}
          </button>
          {open === s.id && (
            <div className="small">
              <p className="muted">Typed as “{TYPE_LABEL[s.story_type] ?? s.story_type}” by the rule {s.rule}. {VERIFY[s.verification]}.</p>
              <ul>
                {s.sources.map((a) => (
                  <li key={a.id}>
                    <span className="muted">{a.source_name}, {fmtWhen(a.seen_at)}: </span>
                    {a.url ? <a href={a.url} target="_blank" rel="noreferrer">{a.title}</a> : a.title}
                  </li>
                ))}
              </ul>
              {s.evidence.some((e) => e.claim !== 'status') && (
                <>
                  <p className="muted">Claims and where they came from:</p>
                  <ul>
                    {s.evidence.filter((e) => e.claim !== 'status').map((e, i) => (
                      <li key={i}>{e.claim === 'release_date' ? 'Release date' : 'Title'}: <strong>{e.value}</strong>
                        <span className="muted"> ({e.url ? <a href={e.url} target="_blank" rel="noreferrer">{e.provider}</a> : e.provider}
                          {e.kind === 'catalog' ? ', catalog' : ''})</span></li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
      ))}
    </>
  )
}

function ArtistName({ a }: { a: StoryArtist }) {
  return <span className="pill trend" title={`tier: ${a.tier}`}>{a.name}{a.plays ? ` 🎧${a.plays}` : ''}</span>
}
