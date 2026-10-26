import { useEffect, useState } from 'react'
import { api, type Alert, type DraftCandidate, type Draft, type RedraftRequest, type ShowFacts } from '../api'

// Ideas with a list of shows you can add to or leave out (tracker/redraft.py LIST_FORMATS)
const LIST_FORMATS = ['weekend', 'stacked_month', 'things_to_do', 'team_week']
const keyOf = (s: ShowFacts) => `${s.artist ?? s.event_name}|${s.date}`

/** "New draft with notes": tell the model what to include, leave out or emphasize. Shows you add come from your
 * event data (so their dates and venues are real); your note steers the wording but can't add facts. */
export function RedraftForm({ alert, previous, busy, templated, onSubmit, onCancel }: {
  alert: Alert; previous: Draft['instructions']; busy: boolean; templated: boolean
  onSubmit: (notes: RedraftRequest) => Promise<void>; onCancel: () => void
}) {
  const shows: ShowFacts[] = alert.facts.shows ?? []
  const listIdea = LIST_FORMATS.includes(String(alert.facts.format)) && shows.length > 0
  const [note, setNote] = useState(previous?.note ?? '')
  const [drop, setDrop] = useState<string[]>((previous?.drop ?? []).filter((k) => shows.some((s) => keyOf(s) === k)))
  const [add, setAdd] = useState<DraftCandidate[]>([])
  const [suggested, setSuggested] = useState<DraftCandidate[] | null>(null)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<DraftCandidate[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!listIdea) return
    api.candidates(alert.id).then((cands) => {
      setSuggested(cands)
      // shows you added last time and that are still listed start out added again
      setAdd(cands.filter((c) => previous?.add?.includes(c.id)))
    }).catch(() => setSuggested([]))
  }, [alert.id, listIdea, previous])

  const search = async () => {
    if (!q.trim()) { setResults(null); return }
    try { setResults(await api.candidates(alert.id, q.trim())) } catch (e) { setError(String(e)) }
  }
  const toggleAdd = (c: DraftCandidate) =>
    setAdd(add.some((a) => a.id === c.id) ? add.filter((a) => a.id !== c.id) : [...add, c])
  const kept = shows.length - drop.length + add.length
  const empty = !note.trim() && !drop.length && !add.length
  const submit = async () => {
    setError(null)
    try { await onSubmit({ note: note.trim() || undefined, add: add.map((a) => a.id), drop }) }
    catch (e) { setError(String(e)) }
  }

  const candidateRow = (c: DraftCandidate) => {
    const on = add.some((a) => a.id === c.id)
    return (
      <li key={c.id} className={on ? 'cand on' : 'cand'}>
        <button className="ghost" onClick={() => toggleAdd(c)}>{on ? '✓ Added' : 'Add'}</button>
        <div>
          <strong>{c.artist}</strong> <span className="muted small">{c.date} · {c.venue}{c.genre ? ` · ${c.genre}` : ''}</span>
          <div className="muted small">Left out: {c.why_not}</div>
        </div>
      </li>
    )
  }

  return (
    <div className="redraft">
      <h3>New draft with notes</h3>
      <div className="field">
        <label htmlFor={`note-${alert.id}`}>Notes for the AI</label>
        <textarea id={`note-${alert.id}`} rows={3} value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. Lead with the biggest show. Don't mention prices. Less hype, more chill." />
        <p className="muted small">
          {templated
            ? 'This format’s script is built from the facts, so your note shapes the hook and hashtags. '
            : 'Your note steers what the draft emphasizes and how it sounds. '}
          It can’t add facts: to include a show, add it below.
        </p>
      </div>

      {listIdea && (
        <>
          <div className="field">
            <label>Shows in this draft ({shows.length - drop.length} kept{add.length ? `, ${add.length} added below` : ''})</label>
            <ul className="redraft-shows">
              {shows.map((s) => {
                const k = keyOf(s)
                return (
                  <li key={k}>
                    <label>
                      <input type="checkbox" checked={!drop.includes(k)}
                        onChange={(e) => setDrop(e.target.checked ? drop.filter((d) => d !== k) : [...drop, k])} />
                      {' '}<strong>{s.artist ?? s.event_name}</strong> <span className="muted small">{s.date} · {s.venue}</span>
                    </label>
                  </li>
                )
              })}
            </ul>
          </div>
          <div className="field">
            <label>Add a show</label>
            {suggested === null ? <p className="muted small">Looking for shows this idea left out…</p>
              : suggested.length === 0 ? <p className="muted small">No close matches in these dates. Search for one below.</p>
              : <>
                  <p className="muted small">Shows in the same dates that this idea left out, closest match first:</p>
                  <ul className="cands">{suggested.map(candidateRow)}</ul>
                </>}
            <div className="redraft-search">
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search any upcoming show"
                onKeyDown={(e) => { if (e.key === 'Enter') search() }} />
              <button className="ghost" onClick={search}>Search</button>
            </div>
            {results && (results.length === 0 ? <p className="muted small">No upcoming shows match “{q}”.</p>
              : <ul className="cands">{results.map(candidateRow)}</ul>)}
          </div>
        </>
      )}

      {error && <div className="warn" role="status">{error}</div>}
      <div className="actions">
        <button onClick={submit} disabled={busy || empty || (listIdea && kept === 0)}>Write draft with these notes</button>
        <button className="ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}
