import { useEffect, useRef, useState } from 'react'
import { api, STATIC, type Alert, type ContextUsage, type Draft, type Job, type MusicNewsItem, type ShowFacts, type WebContext } from '../api'
import { fmtWhen } from '../App'
import { PostForm } from './PostsView'
import { SlidesOverlay } from './SlidesOverlay'

export const KIND_LABEL: Record<Alert['kind'], string> = {
  new_tour: 'Just announced',
  presale_soon: 'Presale / on-sale',
  weekend: 'This weekend',
  stacked_month: 'Stacked month',
  status_change: 'Status change',
  team_week: 'Home games this week',
  big_game: 'Big game',
  music_story: 'Music news',
  new_music_friday: 'New Music Friday',
  upcoming_albums: 'Upcoming albums',
  emerging_artists: 'Artists to watch',
}
// Formats whose script comes from Python (tracker/templates.py, tracker/music_formats.py): an angle drafts instantly
const TEMPLATED = ['presale', 'status_change', 'team_week', 'big_game', 'new_music_friday', 'upcoming_albums', 'emerging_artists']
export const MUSIC_KINDS: Alert['kind'][] = ['music_story', 'new_music_friday', 'upcoming_albums', 'emerging_artists']

/** The script, with the "YOUR TAKE:" beat (music drafts, for you to fill in) marked */
function ScriptText({ text }: { text: string }) {
  return <p>{text.split('\n').map((line, i) => (
    <span key={i}>{i > 0 && '\n'}{line.startsWith('YOUR TAKE:')
      ? <mark className="take" title="your perspective goes here: the draft never invents opinions">{line}</mark> : line}</span>
  ))}</p>
}

// Phones: start long lists (angles) collapsed so a card stays about one screen tall
const NARROW = typeof window !== 'undefined' && window.matchMedia('(max-width: 640px)').matches

const STATUS_LABEL: Record<Draft['status'], string> = {
  pending: 'Pending review',
  approved: 'Approved',
  filmed: 'Filmed',
  rejected: 'Rejected',
}

type Props = { refreshKey: number; busy: boolean; job: Job | null; onJob: (j: Job) => void; onChange: () => void }

export default function AlertsView({ refreshKey, busy, job, onJob, onChange }: Props) {
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
        <AlertCard key={a.id} alert={a} busy={busy} job={job}
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

const NEWS_VERIFY: Record<MusicNewsItem['verification'], string> = {
  unverified: 'one source', corroborated: '2+ outlets', confirmed: 'confirmed', conflicting: 'sources disagree',
}

/** Event ideas: what the acts have been in the music news lately (M8). Headlines to read for your own angle; they're
 * not in the facts, so a draft never repeats them. */
function MusicNewsList({ items }: { items: MusicNewsItem[] }) {
  const [first] = items
  return (
    <details className="web-context">
      <summary>🎵 In the music news ({items.length}): {first.artist}, {first.type_label.toLowerCase()} ({fmtWhen(first.last_seen)})</summary>
      <p className="muted small">From the Music news tab. Not part of the facts: the draft can't use these, so check a story before you mention it.</p>
      <ul>
        {items.map((i) => (
          <li key={i.story_id}>
            <span className="pill">{i.type_label}</span>{' '}
            {i.url ? <a href={i.url} target="_blank" rel="noreferrer">{i.title}</a> : i.title}
            <span className="muted small"> · {fmtWhen(i.last_seen)} · {NEWS_VERIFY[i.verification]}
              {i.release_status ? ` · ${i.release_status}` : ''}{i.sensitive ? ' · sensitive' : ''}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}

export function AlertCard({ alert, busy, job, onRegenerate, onStatus, onDraftChange }: {
  alert: Alert; busy: boolean; job: Job | null; onRegenerate: (angle?: string) => Promise<void>
  onStatus: (s: Alert['status']) => void; onDraftChange: () => void
}) {
  const [showOlder, setShowOlder] = useState(false)
  // Draft progress: "sending" covers the moment between the click and the server accepting the job
  const [sending, setSending] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [done, setDone] = useState<'ok' | 'failed' | null>(null)
  const mine = !!job?.running && job.alert_id === alert.id
  const wasMine = useRef(false)
  useEffect(() => {
    if (wasMine.current && !mine && job) {
      setDone(job.error ? 'failed' : 'ok')
      wasMine.current = false
    }
    if (mine) { wasMine.current = true; setDone(null) }
  }, [mine, job])
  useEffect(() => {  // the success note fades after a few seconds; a failure stays until the next try
    if (done !== 'ok') return
    const t = setTimeout(() => setDone(null), 8000)
    return () => clearTimeout(t)
  }, [done])
  // Once the finished draft has loaded, bring it into view (on a phone it's well above the button)
  const latestRef = useRef<HTMLDivElement>(null)
  const latestId = alert.drafts[0]?.id
  useEffect(() => {
    if (done === 'ok') latestRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [done, latestId])
  const draft = async (angle?: string) => {
    setSending(true); setStartError(null); setDone(null)
    try { await onRegenerate(angle) } catch (e) { setStartError(String(e)) } finally { setSending(false) }
  }
  const [slidesOpen, setSlidesOpen] = useState(false)
  const shows: ShowFacts[] = alert.facts.shows ?? (alert.facts.show ? [alert.facts.show] : [])
  // Roundups: the "my N picks" cut (best by notability), starred in the facts table
  const picks = ((alert.facts.picks as ShowFacts[] | undefined) ?? []).map((s) => `${s.artist}|${s.date}`)
  const [latest, ...older] = alert.drafts
  const music = MUSIC_KINDS.includes(alert.kind)
  const sources = (alert.facts.sources as { outlet: string }[] | undefined) ?? []
  const items = ((alert.facts.releases ?? alert.facts.albums ?? alert.facts.artists_to_watch) as unknown[] | undefined) ?? []

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
        <summary>Facts ({music
          ? alert.kind === 'music_story' ? `${sources.length} source${sources.length === 1 ? '' : 's'}` : `${items.length} listed`
          : `${shows.length} show${shows.length === 1 ? '' : 's'}`}) — the only things the draft is allowed to say</summary>
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
      {alert.music_news && alert.music_news.length > 0 && <MusicNewsList items={alert.music_news} />}

      {alert.angles.length > 0 && (
        <details className="angles" open={!latest && !NARROW}>
          <summary>Angles ({alert.angles.length}): pick one to draft from</summary>
          {alert.angles.map((a) => (
            <div key={a.key} className="angle">
              <div>
                <span className="pill">{a.label}</span> <strong>“{a.hook}”</strong>
                <div className="muted small">{a.pitch} · {a.style} · CTA: {a.cta}</div>
              </div>
              {!STATIC && <button className="ghost" disabled={busy || sending} onClick={() => draft(a.key)}
                                  title={TEMPLATED.includes(String(alert.facts.format))
                                    ? 'templated format: drafted instantly, no model' : 'the model writes the script around this hook (~1 min)'}>
                Draft this
              </button>}
            </div>
          ))}
        </details>
      )}

      <div ref={latestRef}>
        {done === 'ok' && <div className="draft-done" role="status">✓ New draft ready</div>}
        {latest ? <DraftView key={latest.id} draft={latest} alertId={alert.id} onChange={onDraftChange} />
          : <p className="muted">No draft yet.</p>}
      </div>
      {older.length > 0 && (
        <button className="link" onClick={() => setShowOlder(!showOlder)}>
          {showOlder ? 'Hide' : 'Show'} {older.length} older draft{older.length > 1 ? 's' : ''}
        </button>
      )}
      {showOlder && older.map((d) => <DraftView key={d.id} draft={d} alertId={alert.id} onChange={onDraftChange} />)}

      {(mine || sending) && <DraftProgress job={mine ? job : null} />}
      {done === 'failed' && <div className="warn" role="status">Drafting failed: {job?.error}</div>}
      {startError && <div className="warn" role="status">Couldn't start the draft: {startError}</div>}
      <div className="actions">
        {!STATIC && <button onClick={() => draft()} disabled={busy || sending}>
          {mine || sending ? 'Drafting…' : latest ? 'New draft' : 'Write draft'}
        </button>}
        <button className="ghost" onClick={() => setSlidesOpen(true)} title="9:16 slides built from the facts">Slides</button>
        {!STATIC && (alert.status !== 'dismissed'
          ? <button className="ghost" onClick={() => onStatus('dismissed')}>Dismiss</button>
          : <button className="ghost" onClick={() => onStatus(alert.drafts.length ? 'drafted' : 'new')}>Restore</button>)}
      </div>
      {slidesOpen && <SlidesOverlay alertId={alert.id} title={alert.title} onClose={() => setSlidesOpen(false)} />}
    </section>
  )
}

/** A bar that fills over the job's estimated time (the server learns how long one model draft takes on this PC),
 * with the current step underneath. It stops at 95% until the job really finishes. */
function DraftProgress({ job }: { job: Job | null }) {
  // The job is polled every 3 s; tick locally in between so the bar moves smoothly
  const [since, setSince] = useState(() => ({ at: Date.now(), elapsed: job?.elapsed ?? 0 }))
  const [now, setNow] = useState(Date.now())
  useEffect(() => { setSince({ at: Date.now(), elapsed: job?.elapsed ?? 0 }) }, [job?.elapsed])
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(id) }, [])
  const elapsed = since.elapsed + Math.max(0, now - since.at) / 1000
  const estimate = job?.estimate ?? 0
  const pct = estimate ? Math.min(95, (elapsed / estimate) * 100) : 3
  const over = !!estimate && elapsed > estimate
  return (
    <div className="draft-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}
         aria-label="Drafting">
      <div className="bar"><div className={over ? 'fill slow' : 'fill'} style={{ width: `${pct}%` }} /></div>
      <div className="muted small">
        {job ? (job.step ?? 'Working') : 'Starting'}…
        {job && <> · {Math.round(elapsed)}s{estimate > 5 && !over ? ` of about ${estimate}s` : ''}</>}
        {over && ' · taking longer than usual, still going'}
      </div>
    </div>
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
        : key === 'script' ? <ScriptText text={fields[key]} /> : <p className={key === 'hook' ? 'hook' : ''}>{fields[key]}</p>}
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
