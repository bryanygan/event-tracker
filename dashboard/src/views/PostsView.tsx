import { useEffect, useState } from 'react'
import {
  api, COUNT_METRICS, STATIC, VIDEO_STYLES,
  type Analytics, type Checkpoint, type Metrics, type MetricsInput, type Post, type PostInput, type VideoStyle,
} from '../api'
import { fmtWhen } from '../App'

// Alert formats (facts.format) plus "other" for videos that didn't come from an alert.
export const FORMAT_LABEL: Record<string, string> = {
  new_tour: 'Just announced',
  presale: 'Presale / on-sale',
  weekend: 'Weekend roundup',
  stacked_month: 'Stacked month',
  status_change: 'Status change',
  team_week: 'Home games this week',
  big_game: 'Big game',
  other: 'Other',
}

const METRIC_LABEL: Record<string, string> = {
  views: 'Views', likes: 'Likes', comments: 'Comments', shares: 'Shares', saves: 'Saves',
  followers_gained: 'New followers', avg_watch_seconds: 'Avg watch (s)', completion_rate: 'Watched full (%)',
}

// <input type="datetime-local"> works in local time without a zone; the API wants UTC with one.
const localNow = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)
const toUtc = (local: string) => new Date(local).toISOString()
const num = (v: string) => (v.trim() === '' ? undefined : Number(v))

function fmtAge(hours: number) {
  return hours < 48 ? `${Math.round(hours)}h` : `${Math.round(hours / 24)}d`
}

export default function PostsView({ refreshKey, onChange }: { refreshKey: number; onChange: () => void }) {
  const [posts, setPosts] = useState<Post[] | null>(null)
  const [adding, setAdding] = useState(false)
  const load = () => api.posts().then(setPosts)
  useEffect(() => { load() }, [refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const changed = () => { load(); onChange() }

  if (!posts) return <p className="muted">Loading…</p>
  return (
    <>
      <AnalyticsTable refreshKey={refreshKey} posts={posts.length} />
      <div className="toolbar">
        <h3 className="section-title">Posted videos</h3>
        {!STATIC && !adding && <button onClick={() => setAdding(true)}>Log a video</button>}
        <span className="muted small">
          Log videos from a draft with “Log as posted”, or here for anything else. Numbers come from TikTok’s
          analytics; add them about 24 hours and 7 days after posting.
        </span>
      </div>
      {adding && <PostForm onDone={() => { setAdding(false); changed() }} onCancel={() => setAdding(false)} />}
      {posts.length === 0 && <p className="muted">No videos logged yet.</p>}
      {posts.map((p) => <PostCard key={p.id} post={p} onChange={changed} />)}
    </>
  )
}

function AnalyticsTable({ refreshKey, posts }: { refreshKey: number; posts: number }) {
  const [groupBy, setGroupBy] = useState<Analytics['group_by']>('format')
  const [cp, setCp] = useState<Checkpoint>('24h')
  const [data, setData] = useState<Analytics | null>(null)
  useEffect(() => { api.analytics(groupBy).then(setData) }, [groupBy, refreshKey])
  if (!data || posts === 0) return null
  const [lo, hi] = data.checkpoints[cp].window_hours
  const rate = (v: number | null) => (v === null ? '–' : v.toFixed(1))

  return (
    <section className="card analytics">
      <div className="toolbar">
        <h3 className="section-title">How formats compare</h3>
        <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as Analytics['group_by'])}>
          <option value="format">by format</option>
          <option value="video_style">by video style</option>
          <option value="pillar">events vs. music</option>
          <option value="music_format">by music format</option>
        </select>
        <select value={cp} onChange={(e) => setCp(e.target.value as Checkpoint)}>
          <option value="24h">at ~24 hours</option>
          <option value="7d">at ~7 days</option>
        </select>
      </div>
      <div className="table-wrap">
        <table className="events">
          <thead>
            <tr><th>Group</th><th>Videos</th><th>With numbers</th><th>Median views</th>
              <th>Shares /1k</th><th>Saves /1k</th><th>Comments /1k</th><th>Follows /1k</th></tr>
          </thead>
          <tbody>
            {data.groups.map((g) => {
              const c = g.checkpoints[cp]
              return (
                <tr key={g.group} className={c.enough ? '' : 'thin'}>
                  <td>{FORMAT_LABEL[g.group] ?? g.group}</td>
                  <td>{g.posts}</td>
                  <td>{c.n}{!c.enough && <span className="muted small"> · too few to compare</span>}</td>
                  <td>{c.median_views?.toLocaleString() ?? '–'}</td>
                  <td>{rate(c.per_1k_views.shares)}</td>
                  <td>{rate(c.per_1k_views.saves)}</td>
                  <td>{rate(c.per_1k_views.comments)}</td>
                  <td>{rate(c.per_1k_views.followers_gained)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Medians of each video’s numbers recorded {lo}–{hi} hours after posting, so videos are compared at the same
        age. A group needs {data.min_posts}+ videos with numbers before it says anything; “–” means not recorded.
      </p>
    </section>
  )
}

/** Log a published video. From a draft, pass alertId/draftId/hook; the title and format come from the alert. */
export function PostForm({ alertId, draftId, hook = '', onDone, onCancel }: {
  alertId?: number; draftId?: number; hook?: string; onDone: () => void; onCancel: () => void
}) {
  const fromAlert = alertId !== undefined
  const [f, setF] = useState({ posted_at: localNow(), url: '', title: '', format: 'other', video_style: '', length: '', hook, notes: '' })
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })

  const save = async () => {
    setError(null)
    const body: PostInput = {
      alert_id: alertId, draft_id: draftId, posted_at: toUtc(f.posted_at),
      url: f.url.trim() || undefined, hook: f.hook.trim() || undefined, notes: f.notes.trim() || undefined,
      video_style: (f.video_style || undefined) as VideoStyle | undefined, length_seconds: num(f.length),
      ...(fromAlert ? {} : { title: f.title, format: f.format }),
    }
    try {
      await api.createPost(body)
      onDone()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="post-form">
      <div className="form-grid">
        <label>Posted at<input type="datetime-local" value={f.posted_at} onChange={set('posted_at')} /></label>
        <label>TikTok link<input placeholder="https://www.tiktok.com/@…/video/…" value={f.url} onChange={set('url')} /></label>
        {!fromAlert && <label>Title<input value={f.title} onChange={set('title')} /></label>}
        {!fromAlert && (
          <label>Format
            <select value={f.format} onChange={set('format')}>
              {Object.entries(FORMAT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        )}
        <label>Video style
          <select value={f.video_style} onChange={set('video_style')}>
            <option value="">—</option>
            {VIDEO_STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label>Length (s)<input type="number" min="1" value={f.length} onChange={set('length')} /></label>
      </div>
      <label className="wide">Hook you used<input value={f.hook} onChange={set('hook')} /></label>
      <label className="wide">Notes<input placeholder="optional" value={f.notes} onChange={set('notes')} /></label>
      {error && <div className="warn">{error}</div>}
      <div className="actions-inline">
        <button onClick={save}>Save</button>
        <button className="ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function MetricsForm({ postId, onDone, onCancel }: { postId: number; onDone: () => void; onCancel: () => void }) {
  const fields = [...COUNT_METRICS, 'avg_watch_seconds', 'completion_rate'] as const
  const [observed, setObserved] = useState(localNow())
  const [vals, setVals] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    setError(null)
    const body: MetricsInput = { observed_at: toUtc(observed) }
    for (const k of fields) {
      const v = num(vals[k] ?? '')
      if (v !== undefined) body[k] = v
    }
    try {
      await api.addMetrics(postId, body)
      onDone()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="post-form">
      <div className="form-grid">
        <label>Numbers as of<input type="datetime-local" value={observed} onChange={(e) => setObserved(e.target.value)} /></label>
        {fields.map((k) => (
          <label key={k}>{METRIC_LABEL[k]}
            <input type="number" min="0" step={k === 'avg_watch_seconds' || k === 'completion_rate' ? 'any' : '1'}
                   value={vals[k] ?? ''} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
          </label>
        ))}
      </div>
      <p className="muted small">Leave blank anything TikTok doesn’t show you. Blank means unknown, not zero.</p>
      {error && <div className="warn">{error}</div>}
      <div className="actions-inline">
        <button onClick={save}>Save numbers</button>
        <button className="ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function PostCard({ post, onChange }: { post: Post; onChange: () => void }) {
  const [addingNumbers, setAddingNumbers] = useState(false)
  const [editingNotes, setEditingNotes] = useState(false)
  const [notes, setNotes] = useState(post.notes ?? '')
  const used = new Set(Object.values(post.checkpoints).filter((x) => x !== null))
  const cpFor = (m: Metrics) => (Object.entries(post.checkpoints).find(([, id]) => id === m.id)?.[0])

  const saveNotes = async () => { await api.updatePost(post.id, { notes: notes.trim() || null }); setEditingNotes(false); onChange() }
  const remove = async () => {
    if (window.confirm(`Delete “${post.title}” and its numbers?`)) { await api.deletePost(post.id); onChange() }
  }
  const removeMetrics = async (id: number) => { await api.deleteMetrics(id); onChange() }

  return (
    <section className="card post">
      <div className="card-head">
        <span className="pill">{FORMAT_LABEL[post.format ?? ''] ?? post.format ?? 'unknown'}</span>
        {post.video_style && <span className="pill">{post.video_style}</span>}
        <h2>{post.url ? <a href={post.url} target="_blank" rel="noreferrer">{post.title} ↗</a> : post.title}</h2>
        {post.due.map((d) => <span key={d} className="pill due">{d} numbers due</span>)}
        <span className="muted small">posted {fmtWhen(post.posted_at)} · {fmtAge(post.age_hours)} ago</span>
      </div>
      {post.hook && <p className="hook-used">“{post.hook}”{post.length_seconds ? <span className="muted small"> · {post.length_seconds}s</span> : null}</p>}
      {editingNotes
        ? <div className="actions-inline"><input className="grow" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <button onClick={saveNotes}>Save</button><button className="ghost" onClick={() => setEditingNotes(false)}>Cancel</button></div>
        : post.notes && <p className="muted">{post.notes}</p>}

      {post.metrics.length > 0 && (
        <div className="table-wrap">
          <table className="events metrics">
            <thead>
              <tr><th>Age</th>{Object.values(METRIC_LABEL).map((l) => <th key={l}>{l}</th>)}{!STATIC && <th />}</tr>
            </thead>
            <tbody>
              {post.metrics.map((m) => (
                <tr key={m.id} className={used.has(m.id) ? '' : 'thin'}
                    title={used.has(m.id) ? `used for the ${cpFor(m)} comparison` : 'not at a comparison age (~24h or ~7d)'}>
                  <td className="nowrap">{fmtAge(m.age_hours)}{cpFor(m) && <span className="pill new">{cpFor(m)}</span>}</td>
                  {(Object.keys(METRIC_LABEL) as (keyof Metrics)[]).map((k) => <td key={k}>{m[k]?.toLocaleString() ?? '–'}</td>)}
                  {!STATIC && <td><button className="link" onClick={() => removeMetrics(m.id)}>remove</button></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {addingNumbers && <MetricsForm postId={post.id} onDone={() => { setAddingNumbers(false); onChange() }}
                                     onCancel={() => setAddingNumbers(false)} />}
      {!STATIC && !addingNumbers && (
        <div className="actions">
          <button onClick={() => setAddingNumbers(true)}>Add numbers</button>
          <button className="ghost" onClick={() => setEditingNotes(true)}>{post.notes ? 'Edit notes' : 'Add notes'}</button>
          <button className="ghost" onClick={remove}>Delete</button>
        </div>
      )}
    </section>
  )
}
