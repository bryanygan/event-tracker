import { useEffect, useMemo, useState } from 'react'
import { api, STATIC, type Story, type StoryArtist, type StoryFeedback } from '../api'
import { fmtWhen } from '../App'
import { TrendPills } from './TrendNotes'

// Watchlist stories (tracker/stories.py, roadmap M3): articles about the same thing grouped into one card, with
// the rule that typed it and every source. M5 (tracker/music.py): a score with its reasons, a suggested angle,
// your feedback buttons and Send to queue.
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
  confirmed: "matches a release catalog, or the artist's own channel posted it",
  conflicting: 'sources disagree (see the evidence)',
}
const BUTTONS: [StoryFeedback, string, string][] = [
  ['interested', 'Interested', 'More like this: raises this artist and this kind of story'],
  ['not_my_lane', 'Not my lane', 'Less like this: lowers this artist and this kind of story'],
  ['already_covered', 'Already covered', 'You did this one: it moves to the bottom'],
]
const BAND_LABEL = { strong: 'Strong', medium: 'Medium', low: 'Low' } as const

export default function StoriesList({ refreshKey, days, origin, onQueued }: {
  refreshKey: number; days: number; origin: Story['origin']; onQueued?: () => void
}) {
  const [stories, setStories] = useState<Story[] | null>(null)
  const [q, setQ] = useState('')
  const [type, setType] = useState('')
  const [sort, setSort] = useState<'score' | 'recent'>('score')
  const [hideHandled, setHideHandled] = useState(false)
  const [open, setOpen] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => api.stories(days, origin, sort).then(setStories).catch((e) => setError(String(e)))
  useEffect(() => { load() }, [days, origin, sort, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    try { await fn(); await load() } catch (e) { setError(String(e)) }
  }
  const feedback = (s: Story, f: StoryFeedback) => act(() => api.storyFeedback(s.id, s.feedback === f ? null : f))
  const send = (s: Story) => act(async () => { await api.sendStory(s.id); onQueued?.() })

  const types = useMemo(() => [...new Set((stories ?? []).map((s) => s.story_type))].sort(), [stories])
  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return (stories ?? []).filter((s) => (!type || s.story_type === type) &&
      (!hideHandled || (!s.feedback || s.feedback === 'interested') && !s.alert_id) &&
      (!ql || s.title.toLowerCase().includes(ql) || [s.primary_artist, ...s.other_artists]
        .some((a) => a?.name.toLowerCase().includes(ql))))
  }, [stories, q, type, hideHandled])

  return (
    <>
      <div className="toolbar">
        <input placeholder="Search stories or artists" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All kinds</option>
          {types.map((t) => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value as 'score' | 'recent')}>
          <option value="score">Best first</option>
          <option value="recent">Newest first</option>
        </select>
        <label className="small" title="Hide stories you marked Not my lane or Already covered, or sent to the queue">
          <input type="checkbox" style={{ width: 'auto' }} checked={hideHandled} onChange={(e) => setHideHandled(e.target.checked)} /> Hide handled
        </label>
        <span className="muted small">{stories ? `${shown.length} stories` : 'Loading…'}</span>
      </div>
      {error && <div className="banner error">{error}</div>}
      {stories && shown.length === 0 && <p className="muted">No stories about your watchlist artists in this window yet.</p>}
      {shown.map((s) => (
        <div key={s.id} className={`card${s.feedback === 'not_my_lane' || s.feedback === 'already_covered' ? ' thin' : ''}`}>
          <div className="card-head">
            <div>
              <div className="trend-pills">
                {s.band && <span className={`pill band ${s.band}`} title={`music score ${s.score} (experimental, rules ${s.rules_version})`}>
                  {BAND_LABEL[s.band]} · {Math.round(s.score ?? 0)}</span>}
                <span className="pill" title={s.rule ?? ''}>{TYPE_LABEL[s.story_type] ?? s.story_type}</span>
                {s.release_status && <span className="pill">{s.release_status}</span>}
                <span className={`pill${s.verification === 'conflicting' ? ' pill-warn' : ''}`} title={VERIFY[s.verification]}>
                  {s.origin === 'catalog' ? 'catalog' : `${s.source_count} source${s.source_count === 1 ? '' : 's'}`}
                  {s.verification === 'conflicting' ? ' · conflicting' : s.verification === 'confirmed' ? ' · confirmed' : ''}
                </span>
                {s.sensitive && <span className="pill pill-warn" title="Deaths, arrests, lawsuits, health: handle with care; never promoted automatically">sensitive</span>}
                {s.alert_id && <span className="pill new" title="it's an idea in What to post now">in the queue</span>}
                {s.feedback && <span className="pill">{BUTTONS.find(([k]) => k === s.feedback)?.[1]}</span>}
              </div>
              <strong>{s.title}</strong>
              <div className="small">
                {[s.primary_artist, ...s.other_artists].filter(Boolean).map((a) => <ArtistName key={a!.id} a={a!} />)}
                {s.release_title && <span className="muted"> · “{s.release_title}”</span>}
                {s.release_date && <span className="muted"> · release date {s.release_date}
                  {s.evidence.some((e) => e.kind === 'catalog') ? ' (per the catalogs)' : ' (as reported)'}</span>}
                {s.philly_shows.length > 0 && <span className="muted"> · Philly: {s.philly_shows.map((p) => `${p.date}, ${p.venue}`).join('; ')}</span>}
              </div>
            </div>
            <span className="muted small nowrap" style={{ marginLeft: 'auto' }} title={s.dated ? '' : 'the feed gives no date; it was already there when tracking began'}>
              {s.dated ? fmtWhen(s.last_seen) : 'before tracking'}
            </span>
          </div>
          {s.angle && <p className="hook-used muted small" title="a suggestion from the kind of story, not a fact">Angle: {s.angle}</p>}
          <TrendPills matches={s.trend_notes} />
          {s.reasons.length > 0 && <ul className="reasons small">{s.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
          {s.caveats.length > 0 && <div className="warn small"><ul>{s.caveats.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}
          <div className="actions">
            {!STATIC && <>
              {s.alert_id
                ? <span className="muted small">✓ Sent to What to post</span>
                : <button onClick={() => send(s)} title="Make it a video idea in What to post, with these sources as its facts">Send to queue</button>}
              {BUTTONS.map(([k, label, help]) => (
                <button key={k} className={s.feedback === k ? '' : 'ghost'} title={s.feedback === k ? 'click again to clear' : help}
                        onClick={() => feedback(s, k)}>{s.feedback === k ? `✓ ${label}` : label}</button>
              ))}
            </>}
            <button className="ghost" onClick={() => setOpen(open === s.id ? null : s.id)}>
              {open === s.id ? 'Hide details' : s.origin === 'catalog' ? 'Score and where it’s listed' : `Score and sources (${s.sources.length})`}
            </button>
          </div>
          {open === s.id && (
            <div className="small">
              <table>
                <tbody>
                  {s.components.map((c) => (
                    <tr key={c.key} className={c.value === null ? 'thin' : ''}>
                      <td>{c.label}</td><td className="nowrap">{c.value ?? 'unknown'}</td>
                      <td className="muted nowrap">× {c.weight}</td><td>{c.why}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted">Typed as “{TYPE_LABEL[s.story_type] ?? s.story_type}” by the rule {s.rule}. {VERIFY[s.verification]}.
                Score {s.score ?? '–'} ({Math.round(s.coverage * 100)}% of signals known): experimental, a hint, not a prediction.</p>
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
                          {e.kind === 'catalog' ? ', catalog' : e.kind === 'official' ? ", the artist's own channel" : ''})</span></li>
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
  return <span className="pill trend" title={`tier: ${a.tier}${a.seen_live ? ' · seen live' : ''}`}>
    {a.name}{a.plays ? ` 🎧${a.plays}` : ''}{a.seen_live ? ' 🎤' : ''}</span>
}
