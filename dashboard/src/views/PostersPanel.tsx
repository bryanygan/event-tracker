// Show posters for an event idea: each show's listing image (tracker/posters.py) and the venue's or artist's
// Instagram posts that match it (tracker/instagram.py). Loaded when opened, since opening may save a few.
// A poster is a picture, not a fact: warnings say when the listing changed after it was made.
import { useEffect, useState } from 'react'
import { api, type Poster } from '../api'

// Phones share files to the share sheet, whose "Save N Images" puts them in Photos (a download link on iOS goes
// to Files instead). Computers get ordinary downloads.
const PHONE = typeof matchMedia !== 'undefined' && matchMedia('(pointer: coarse)').matches

// Each saved poster as a file, named the way its own Download link names it
async function asFiles(list: Poster[]): Promise<File[]> {
  const got = await Promise.all(list.map(async (p) => {
    const r = await fetch(p.download!)
    if (!r.ok) throw new Error(`Couldn't get the poster for ${p.name} (${r.status})`)
    const cd = r.headers.get('content-disposition') ?? ''
    const m = /filename\*=utf-8''([^;]+)/i.exec(cd) ?? /filename="([^"]+)"/i.exec(cd)
    let name = `${p.key}.jpg`
    try { if (m) name = decodeURIComponent(m[1]) } catch { /* keep the fallback */ }
    return { name, blob: await r.blob() }
  }))
  const used = new Set<string>()
  return got.map(({ name, blob }) => {
    const dot = name.lastIndexOf('.')
    let unique = name
    for (let i = 2; used.has(unique); i++) unique = `${name.slice(0, dot)} (${i})${name.slice(dot)}`
    used.add(unique)
    return new File([blob], unique, { type: blob.type })
  })
}

export default function PostersPanel({ alertId }: { alertId: number }) {
  const [posters, setPosters] = useState<Poster[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [files, setFiles] = useState<File[] | null>(null)
  const load = () => {
    if (posters || error) return
    api.posters(alertId).then(setPosters).catch((e) => setError(String(e.message ?? e)))
  }
  // A show's artist press photo only shows when that show has no poster at all
  const shown = posters?.filter((p) => p.kind !== 'artist_photo'
    || !posters.some((q) => q.event_id === p.event_id && q.kind !== 'artist_photo')) ?? null
  const saved = shown?.filter((p) => p.saved) ?? []
  const savable = saved.filter((p) => p.download)
  // Fetched ahead of the tap: iOS only opens the share sheet straight from a tap, not after a wait
  useEffect(() => {
    if (!posters || savable.length === 0) return
    asFiles(savable).then(setFiles).catch((e) => setError(String(e.message ?? e)))
  }, [posters]) // eslint-disable-line react-hooks/exhaustive-deps
  const saveAll = async () => {
    if (!files) return
    if (PHONE && navigator.canShare?.({ files })) {
      try { await navigator.share({ files }) } catch (e) {
        if ((e as Error).name !== 'AbortError') setError(`Couldn't open the share sheet: ${(e as Error).message}`)
      }
      return
    }
    for (const f of files) {
      const a = document.createElement('a')
      a.href = URL.createObjectURL(f)
      a.download = f.name
      a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 60_000)
      await new Promise((r) => setTimeout(r, 300))
    }
  }
  return (
    <details className="web-context posters" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) load() }}>
      <summary>🖼 Posters{shown ? ` (${saved.length} saved${shown.length > saved.length ? `, ${shown.length - saved.length} other` : ''})` : ''}</summary>
      <p className="muted small">
        From each show's listing and from the venue's and artist's Instagram. They belong to the venue or artist:
        credit them in the video, and go by the facts, not the poster, for dates and openers.
      </p>
      {error && <p className="warn small">{error}</p>}
      {!posters && !error && <p className="muted small">Loading… (the first time can take a few seconds)</p>}
      {shown && shown.length === 0 && <p className="muted small">No posters found for these shows yet.</p>}
      {savable.length > 0 && (
        <p className="small">
          <button className="ghost" onClick={saveAll} disabled={!files}>
            {!files ? 'Getting posters ready…' : PHONE ? `Save all ${files.length} to Photos` : `Download all ${files.length}`}
          </button>
          {PHONE && files && <span className="muted"> then tap "Save {files.length === 1 ? 'Image' : `${files.length} Images`}"</span>}
        </p>
      )}
      {shown && shown.length > 0 && (
        <div className="poster-grid">
          {shown.map((p) => (
            <figure key={p.key} className="poster">
              {p.image
                ? <a href={p.image} target="_blank" rel="noreferrer"><img src={p.image} alt={`${p.name} poster`} loading="lazy" /></a>
                : p.kind === 'artist_photo'
                ? <a href={p.source_url} target="_blank" rel="noreferrer"><img src={p.source_url} alt={`${p.name} press photo`} loading="lazy" referrerPolicy="no-referrer" /></a>
                : <a className="poster-missing muted small" href={p.source_url} target="_blank" rel="noreferrer">
                    {p.kind === 'instagram' ? `Open the post${p.error ? ` (${p.error})` : ''} ↗`
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
