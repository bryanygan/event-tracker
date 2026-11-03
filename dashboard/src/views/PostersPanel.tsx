// Show posters for an event idea: each show's listing image (tracker/posters.py) and the venue's or artist's
// Instagram posts that match it (tracker/instagram.py). Loaded when opened, since opening may save a few.
// A poster is a picture, not a fact: warnings say when the listing changed after it was made.
import { useState } from 'react'
import { api, type Poster } from '../api'

export default function PostersPanel({ alertId }: { alertId: number }) {
  const [posters, setPosters] = useState<Poster[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () => {
    if (posters || error) return
    api.posters(alertId).then(setPosters).catch((e) => setError(String(e.message ?? e)))
  }
  const saved = posters?.filter((p) => p.saved) ?? []
  return (
    <details className="web-context posters" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) load() }}>
      <summary>🖼 Posters{posters ? ` (${saved.length} saved${posters.length > saved.length ? `, ${posters.length - saved.length} other` : ''})` : ''}</summary>
      <p className="muted small">
        From each show's listing and from the venue's and artist's Instagram. They belong to the venue or artist:
        credit them in the video, and go by the facts, not the poster, for dates and openers.
      </p>
      {error && <p className="warn small">{error}</p>}
      {!posters && !error && <p className="muted small">Loading… (the first time can take a few seconds)</p>}
      {posters && posters.length === 0 && <p className="muted small">No posters found for these shows yet.</p>}
      {posters && posters.length > 0 && (
        <div className="poster-grid">
          {posters.map((p) => (
            <figure key={p.key} className="poster">
              {p.image
                ? <a href={p.image} target="_blank" rel="noreferrer"><img src={p.image} alt={`${p.name} poster`} loading="lazy" /></a>
                : <a className="poster-missing muted small" href={p.source_url} target="_blank" rel="noreferrer">
                    {p.kind === 'artist_photo' ? 'Artist photo ↗'
                      : p.kind === 'instagram' ? `Open the post${p.error ? ` (${p.error})` : ''} ↗`
                      : `Not saved${p.error ? `: ${p.error}` : ''} ↗`}
                  </a>}
              <figcaption className="small">
                <span className="pill">{p.label}</span>
                <div><strong>{p.name}</strong></div>
                <div className="muted">{p.venue} · {p.date}</div>
                {p.kind === 'instagram' && (
                  <div className="muted" title={p.caption}>
                    Posted {p.posted_at?.slice(0, 10)}: {p.match} · <a href={p.source_url} target="_blank" rel="noreferrer">post ↗</a>
                  </div>
                )}
                {p.download && <a href={p.download} download>Download</a>}
                {p.image && p.credit && <div className="muted">Credit: {p.credit}</div>}
                {p.warnings.map((w, i) => <div key={i} className="warn">⚠ {w}</div>)}
              </figcaption>
            </figure>
          ))}
        </div>
      )}
    </details>
  )
}
