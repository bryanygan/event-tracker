// Full-screen 9:16 slide viewer/editor for one idea. Words come from the facts (tracker/slides.py);
// edits are text only, plus optional images you have the rights to. Read-only in the static snapshot.
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, STATIC, type Slide, type SlideDeck, type SlideImage, type SlideManifest, type SlideOverride } from '../api'
import { renderSlide, toBlob } from '../slides/render'

type Overrides = SlideDeck['overrides']

const LABELS: Record<string, string> = {
  kicker: 'Top label', headline: 'Headline', sub: 'Subtitle', note: 'Small print',
  date: 'Date', time: 'Time', artist: 'Artist / event', venue: 'Venue', extra: 'Extra line',
  heading: 'Heading', lines: 'Lines (one per line)',
}
const KIND_NAME: Record<Slide['kind'], string> = { title: 'Title', show: 'Show', sales: 'Ticket times', cta: 'Call to action' }

// Mirrors slides.apply() so edits redraw instantly before they're saved
function applyOverrides(base: SlideManifest, o: Overrides): SlideManifest {
  const out: SlideManifest = JSON.parse(JSON.stringify(base))
  const brand = o.deck?.brand
  if (typeof brand === 'string') out.brand = brand
  for (const s of out.slides) {
    const mine = o[s.id] ?? {}
    for (const [k, v] of Object.entries(mine)) if (k in s.fields && typeof v === 'string') s.fields[k] = v
    s.edited = Object.keys(mine).filter((k) => k in s.fields)
    s.hidden = mine.hidden === true
    if (mine.image) s.image = mine.image as SlideImage
  }
  return out
}

// Drop no-op edits (text equal to the default) and empty entries before saving
function tidy(base: SlideManifest, o: Overrides): Overrides {
  const out: Overrides = {}
  for (const [sid, entry] of Object.entries(o)) {
    const slide = base.slides.find((s) => s.id === sid)
    const kept: SlideOverride = {}
    for (const [k, v] of Object.entries(entry)) {
      if (v === null || v === false) continue
      if (slide && k in slide.fields && slide.fields[k] === v) continue
      if (sid === 'deck' && k === 'brand' && v === base.brand) continue
      kept[k] = v
    }
    if (Object.keys(kept).length) out[sid] = kept
  }
  return out
}

// Shrink an uploaded photo to at most 1080 px wide (JPEG) so it stays well under the server's size cap
async function readImage(file: File): Promise<string> {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const i = new Image()
      i.onload = () => resolve(i)
      i.onerror = () => reject(new Error('Could not read that image'))
      i.src = url
    })
    const scale = Math.min(1, 1080 / img.width, 1350 / img.height)
    const c = document.createElement('canvas')
    c.width = Math.round(img.width * scale)
    c.height = Math.round(img.height * scale)
    c.getContext('2d')!.drawImage(img, 0, 0, c.width, c.height)
    for (const q of [0.85, 0.7, 0.55]) {
      const data = c.toDataURL('image/jpeg', q)
      if (data.length < 1_900_000) return data
    }
    throw new Error('Image is too large even after shrinking')
  } finally {
    URL.revokeObjectURL(url)
  }
}

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

export function SlidesOverlay({ alertId, title, onClose }: { alertId: number; title: string; onClose: () => void }) {
  const [deck, setDeck] = useState<SlideDeck | null>(null)
  const [overrides, setOverrides] = useState<Overrides>({})
  const [dirty, setDirty] = useState(false)
  const [selected, setSelected] = useState(0)
  const [previews, setPreviews] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string[] | null>(null)
  const rendered = useRef<Record<string, string>>({}) // slide id -> what was drawn (skip unchanged slides)
  const canvases = useRef<Record<string, HTMLCanvasElement>>({})
  const urls = useRef<Record<string, string>>({}) // preview object URLs, revoked when replaced or on close

  const load = (d: SlideDeck) => {
    setDeck(d)
    setOverrides(d.overrides ?? {})
    setDirty(false)
  }
  useEffect(() => {
    api.slides(alertId).then(load).catch((e) => setError(String(e.message ?? e)))
  }, [alertId])

  const close = () => {
    if (dirty && !window.confirm('Close without saving your slide edits?')) return
    onClose()
  }
  // Esc closes; the page behind doesn't scroll while the overlay is open
  const closeRef = useRef(close)
  useEffect(() => { closeRef.current = close })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeRef.current() }
    window.addEventListener('keydown', onKey)
    const before = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = before }
  }, [])

  const manifest = useMemo(() => (deck ? applyOverrides(deck.base, overrides) : null), [deck, overrides])
  const visible = manifest ? manifest.slides.filter((s) => !s.hidden) : []

  // Redraw previews (debounced while typing); only slides whose content or position changed
  useEffect(() => {
    if (!manifest) return
    let cancelled = false
    const t = setTimeout(async () => {
      for (const s of manifest.slides) {
        const pos = visible.indexOf(s)
        const key = JSON.stringify([s, manifest.brand, manifest.theme, pos, visible.length])
        if (rendered.current[s.id] === key) continue
        const canvas = (canvases.current[s.id] ??= document.createElement('canvas'))
        await renderSlide(canvas, manifest, s, Math.max(pos, 0), visible.length)
        const url = URL.createObjectURL(await toBlob(canvas))
        if (cancelled) { URL.revokeObjectURL(url); return }
        rendered.current[s.id] = key
        const old = urls.current[s.id]
        urls.current[s.id] = url
        if (old) URL.revokeObjectURL(old)
        setPreviews({ ...urls.current })
      }
    }, 200)
    return () => { cancelled = true; clearTimeout(t) }
  }, [manifest]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => Object.values(urls.current).forEach((u) => URL.revokeObjectURL(u)), [])

  const edit = (sid: string, key: string, value: SlideOverride[string]) => {
    setOverrides((o) => ({ ...o, [sid]: { ...(o[sid] ?? {}), [key]: value } }))
    setDirty(true)
    setNotice(null)
  }
  const resetField = (sid: string, key: string) => {
    setOverrides((o) => {
      const entry = { ...(o[sid] ?? {}) }
      delete entry[key]
      return { ...o, [sid]: entry }
    })
    setDirty(true)
  }

  const run = async (fn: () => Promise<SlideDeck>, after?: (d: SlideDeck) => void) => {
    setBusy(true); setError(null)
    try {
      const d = await fn()
      load(d)
      after?.(d)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setBusy(false)
    }
  }
  const save = () => deck && run(() => api.saveSlides(alertId, tidy(deck.base, overrides)), () => setNotice(['Saved.']))
  const rebuild = () => run(() => api.rebuildSlides(alertId),
    (d) => setNotice(d.dropped?.length ? ['Rebuilt from the current facts. Edits dropped because the fact under them changed:', ...d.dropped]
                                       : ['Rebuilt from the current facts. All your edits still apply.']))
  const resetAll = () => {
    if (window.confirm('Throw away all edits and images on these slides?')) run(() => api.resetSlides(alertId), () => setNotice(['Back to the defaults.']))
  }

  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || `idea-${alertId}`
  const fileName = (s: Slide) => `${slug}-${String(visible.indexOf(s) + 1).padStart(2, '0')}.png`
  const downloadOne = async (s: Slide) => {
    const c = canvases.current[s.id]
    if (c) download(await toBlob(c), fileName(s))
  }
  const downloadAll = async () => {
    for (const s of visible) {
      await downloadOne(s)
      await new Promise((r) => setTimeout(r, 350)) // browsers drop rapid back-to-back downloads
    }
  }

  const slide = manifest?.slides[Math.min(selected, manifest.slides.length - 1)]
  const base = slide && deck?.base.slides.find((s) => s.id === slide.id)

  return (
    <div className="slides-overlay" role="dialog" aria-modal="true" aria-label={`Slides for ${title}`}>
      <header className="slides-head">
        <div className="slides-title">
          <strong>Slides</strong>
          <span className="muted small">{visible.length} of {manifest?.slides.length ?? 0} · 1080×1920</span>
        </div>
        <button className="ghost" onClick={close} aria-label="Close slides">✕</button>
      </header>

      {error && <div className="banner error">{error}</div>}
      {!deck && !error && <p className="muted">Drawing slides…</p>}
      {deck && deck.facts_changes.length > 0 && (
        <div className="banner">
          <strong>Facts changed since these slides were saved.</strong>
          <ul>{deck.facts_changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
          {!STATIC && <button disabled={busy || dirty} onClick={rebuild} title={dirty ? 'save or undo your edits first' : ''}>Rebuild from facts</button>}
        </div>
      )}
      {notice && <div className="banner">{notice.length === 1 ? notice[0] : <><p>{notice[0]}</p><ul>{notice.slice(1).map((n, i) => <li key={i}>{n}</li>)}</ul></>}</div>}
      {manifest && manifest.shows_total > manifest.shows_on_slides && (
        <p className="muted small">{manifest.shows_on_slides} of {manifest.shows_total} shows have a slide. Hide some to make a tighter 3–5 pick.</p>
      )}

      {manifest && (
        <div className="slides-strip">
          {manifest.slides.map((s, i) => (
            <button key={s.id} className={`slide-thumb${i === selected ? ' selected' : ''}${s.hidden ? ' hidden-slide' : ''}`}
                    onClick={() => setSelected(i)} aria-label={`${KIND_NAME[s.kind]} slide${s.hidden ? ' (skipped)' : ''}`}>
              {previews[s.id] ? <img src={previews[s.id]} alt="" /> : <span className="muted small">…</span>}
              {s.hidden && <span className="slide-badge">Skipped</span>}
            </button>
          ))}
        </div>
      )}

      {manifest && slide && (
        <div className="slides-editor">
          <div className="row wrap">
            <strong>{KIND_NAME[slide.kind]} slide</strong>
            {!slide.hidden && <button className="ghost" onClick={() => downloadOne(slide)} disabled={!previews[slide.id]}>Download</button>}
            <button className="ghost" onClick={downloadAll} disabled={visible.some((s) => !previews[s.id])}>Download all ({visible.length})</button>
          </div>

          {STATIC ? (
            <p className="muted small">Read-only snapshot: edit slides on the PC dashboard.</p>
          ) : (
            <>
              <label className="check">
                <input type="checkbox" checked={!!slide.hidden} onChange={(e) => edit(slide.id, 'hidden', e.target.checked)} />
                Skip this slide
              </label>
              {Object.entries(slide.fields).map(([k, v]) => (
                <div key={k} className="slide-field">
                  <label htmlFor={`f-${k}`}>{LABELS[k] ?? k}{slide.edited?.includes(k) && <span className="pill">edited</span>}</label>
                  <div className="row">
                    {k === 'lines'
                      ? <textarea id={`f-${k}`} rows={4} value={v} maxLength={1200} onChange={(e) => edit(slide.id, k, e.target.value)} />
                      : <input id={`f-${k}`} className="grow" value={v} maxLength={300} onChange={(e) => edit(slide.id, k, e.target.value)} />}
                    {slide.edited?.includes(k) && (
                      <button className="ghost" title={`back to: ${base?.fields[k] ?? ''}`} onClick={() => resetField(slide.id, k)}>↺</button>
                    )}
                  </div>
                </div>
              ))}
              {(slide.kind === 'title' || slide.kind === 'show') && (
                <ImageEditor image={slide.image} rights={deck!.rights}
                             onChange={(img) => edit(slide.id, 'image', img)} onError={setError} />
              )}
              <details className="slide-deck-opts">
                <summary>Deck options</summary>
                <div className="slide-field">
                  <label htmlFor="f-brand">Brand line (top of every slide)</label>
                  <input id="f-brand" value={manifest.brand} maxLength={60} onChange={(e) => edit('deck', 'brand', e.target.value)} />
                </div>
                <button className="ghost" onClick={resetAll} disabled={busy || !deck!.saved}>Reset all slides to the facts</button>
              </details>
              <div className="slides-save">
                <button onClick={save} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save edits'}</button>
                {dirty && <span className="muted small">Unsaved changes</span>}
              </div>
            </>
          )}
          {slide.image && (
            <p className="muted small">
              Image credit: {slide.image.credit} · {deck!.rights[slide.image.rights] ?? slide.image.rights}
            </p>
          )}
          <p className="muted small">
            No artist photos or music are added for you: only images you have the rights to. Dates and venues come from the
            facts; if you change one by hand, double-check it before posting.
          </p>
        </div>
      )}
    </div>
  )
}

function ImageEditor({ image, rights, onChange, onError }: {
  image?: SlideImage; rights: Record<string, string>
  onChange: (img: SlideImage | null) => void; onError: (msg: string) => void
}) {
  const [data, setData] = useState<string | null>(null)
  const [credit, setCredit] = useState('')
  const [basis, setBasis] = useState('')

  if (image) {
    return (
      <div className="slide-field">
        <label>Image</label>
        <div className="row wrap">
          <span className="small">{image.credit} · {rights[image.rights] ?? image.rights}</span>
          <button className="ghost" onClick={() => onChange(null)}>Remove image</button>
        </div>
      </div>
    )
  }
  return (
    <details className="slide-image">
      <summary>Add your own image (optional)</summary>
      <p className="muted small">Only photos you took or have permission or a license to use. A credit is required.</p>
      <input type="file" accept="image/png,image/jpeg,image/webp"
             onChange={async (e) => {
               const f = e.target.files?.[0]
               if (!f) return
               try { setData(await readImage(f)) } catch (err) { onError(String((err as Error).message)) }
             }} />
      <div className="slide-field">
        <label htmlFor="img-credit">Credit</label>
        <input id="img-credit" value={credit} maxLength={200} placeholder="e.g. Photo by me / @photographer" onChange={(e) => setCredit(e.target.value)} />
      </div>
      <div className="slide-field">
        <label htmlFor="img-rights">Why you can use it</label>
        <select id="img-rights" value={basis} onChange={(e) => setBasis(e.target.value)}>
          <option value="">Choose…</option>
          {Object.entries(rights).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      </div>
      <button disabled={!data || !credit.trim() || !basis}
              onClick={() => { onChange({ data_url: data!, credit: credit.trim(), rights: basis }); setData(null); setCredit(''); setBasis('') }}>
        Use this image
      </button>
    </details>
  )
}
