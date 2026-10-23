import { useEffect, useMemo, useState } from 'react'
import { api, type WatchArtist, type WatchTier } from '../api'

const TIERS: WatchTier[] = ['core', 'adjacent', 'watch', 'breakout', 'dropped']
const TIER_HELP: Record<WatchTier, string> = {
  core: 'you play them a lot (25+ plays) or put them here',
  adjacent: 'close to your core artists',
  watch: 'keeping an eye on them',
  breakout: 'new and growing',
  dropped: 'out of your rotation: kept, but not matched',
}
const PAGE = 40

// The music watchlist (tracker/watchlist.py, roadmap M2). Seeded from your listening history; anything you change
// here is yours and survives re-seeding.
export default function WatchlistPanel({ refreshKey }: { refreshKey: number }) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<WatchArtist[] | null>(null)
  const [q, setQ] = useState('')
  const [tier, setTier] = useState<WatchTier | ''>('')
  const [shown, setShown] = useState(PAGE)
  const [name, setName] = useState('')
  const [newTier, setNewTier] = useState<WatchTier>('watch')
  const [error, setError] = useState<string | null>(null)

  const load = () => api.watchlist().then(setRows).catch((e) => setError(String(e)))
  useEffect(() => { if (open) load() }, [open, refreshKey])

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return (rows ?? []).filter((r) => (!tier || r.tier === tier) &&
      (!ql || r.name.toLowerCase().includes(ql) || r.aliases.some((a) => a.toLowerCase().includes(ql))))
  }, [rows, q, tier])

  const save = async (r: WatchArtist, fields: Parameters<typeof api.updateWatch>[1]) => {
    setError(null)
    try {
      const updated = await api.updateWatch(r.id, fields)
      setRows((all) => all?.map((x) => (x.id === r.id ? updated : x)) ?? null)
    } catch (e) { setError(String(e)) }
  }

  const add = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setError(null)
    try {
      await api.addWatch(name.trim(), newTier)
      setName('')
      await load()
    } catch (err) { setError(String(err)) }
  }

  const counts = useMemo(() => TIERS.map((t) => [t, (rows ?? []).filter((r) => r.tier === t).length] as const), [rows])
  return (
    <>
      <h3>
        <button className="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? '▾' : '▸'} Watchlist
        </button>
        {rows && <span className="muted small"> {counts.filter(([, n]) => n).map(([t, n]) => `${n} ${t}`).join(' · ')}</span>}
      </h3>
      {open && (
        <div className="trend-panel">
          <p className="muted small">
            Built from your listening history (artists with 25+ plays are “core”). Headlines that name them are tagged
            above. Change a tier, mute someone, or add spellings; your changes stay when the list is rebuilt. Play
            counts are read live from your history and never stored here.
          </p>
          {error && <div className="banner error">{error}</div>}
          <form className="toolbar" onSubmit={add}>
            <input placeholder="Add an artist" value={name} onChange={(e) => setName(e.target.value)} />
            <select value={newTier} onChange={(e) => setNewTier(e.target.value as WatchTier)}>
              {TIERS.filter((t) => t !== 'dropped').map((t) => <option key={t} value={t} title={TIER_HELP[t]}>{t}</option>)}
            </select>
            <button type="submit" disabled={!name.trim()}>Add</button>
          </form>
          <div className="toolbar">
            <input placeholder="Search the watchlist" value={q} onChange={(e) => { setQ(e.target.value); setShown(PAGE) }} />
            <select value={tier} onChange={(e) => { setTier(e.target.value as WatchTier | ''); setShown(PAGE) }}>
              <option value="">All tiers</option>
              {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <span className="muted small">{rows ? `${filtered.length} artists` : 'Loading…'}</span>
          </div>
          <div className="table-wrap">
            <table className="events">
              <thead>
                <tr><th>Artist</th><th>Tier</th><th title="plays in the listening window">🎧</th><th title="articles in the last 30 days">News 30d</th>
                  <th>Other spellings</th><th title="An ordinary word too? Then a headline needs a music word (album, tour…) to count">Common word</th><th>Mute</th></tr>
              </thead>
              <tbody>
                {filtered.slice(0, shown).map((r) => (
                  <tr key={r.id} className={r.muted || r.tier === 'dropped' ? 'muted' : ''}>
                    <td>
                      {r.name}
                      {r.origin === 'manual' && <span className="muted small"> · added by you</span>}
                      {r.ambiguous_name && <span className="muted small" title="MusicBrainz lists several artists with this name, so a mention only in a summary doesn't count"> · shared name</span>}
                    </td>
                    <td>
                      <select value={r.tier} title={TIER_HELP[r.tier]} onChange={(e) => save(r, { tier: e.target.value as WatchTier })}>
                        {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </td>
                    <td>{r.plays || ''}</td>
                    <td>{r.articles_30d || ''}</td>
                    <td>
                      <input defaultValue={r.aliases.join(', ')} placeholder="e.g. a nickname" key={r.aliases.join('|')}
                             onBlur={(e) => {
                               const next = e.target.value.split(',').map((x) => x.trim()).filter(Boolean)
                               if (next.join('|') !== r.aliases.join('|')) save(r, { aliases: next })
                             }} />
                    </td>
                    <td>
                      <select value={r.common_word_override == null ? 'auto' : r.common_word_override ? 'yes' : 'no'}
                              onChange={(e) => save(r, { common_word_override: e.target.value === 'auto' ? null : e.target.value === 'yes' })}>
                        <option value="auto">auto ({r.common_word_auto ? 'yes' : 'no'})</option>
                        <option value="yes">yes</option>
                        <option value="no">no</option>
                      </select>
                    </td>
                    <td><input type="checkbox" checked={r.muted} onChange={(e) => save(r, { muted: e.target.checked })} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {filtered.length > shown && <button className="ghost" onClick={() => setShown(shown + PAGE * 2)}>Show more ({filtered.length - shown} left)</button>}
        </div>
      )}
    </>
  )
}
