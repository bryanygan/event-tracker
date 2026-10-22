// Draws one 1080x1920 slide from a manifest (tracker/slides.py decides the words; this decides the look).
// Pure canvas, no network: user images are data: URLs, so the canvas stays exportable as PNG.
import type { Slide, SlideManifest } from '../api'

export const W = 1080
export const H = 1920
// TikTok covers roughly the bottom 400px (caption, sound) and a strip on the right (buttons): keep text out.
const SAFE = { left: 84, right: 930, top: 170, bottom: 1480 }
const FONT = '"Segoe UI", "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif'

type Palette = { top: string; bottom: string; glow: string; glow2: string; accent: string; onAccent: string }
// Mood per main genre (THEMES in tracker/slides.py)
export const PALETTES: Record<string, Palette> = {
  night: { top: '#101119', bottom: '#2a2141', glow: '#6c5ce7', glow2: '#ff7ab6', accent: '#ffd166', onAccent: '#1a1408' },
  ember: { top: '#170b07', bottom: '#4a1608', glow: '#ff4d00', glow2: '#ffb000', accent: '#ff8a2a', onAccent: '#1d0b02' },
  plum: { top: '#140a1d', bottom: '#3e1652', glow: '#a23bd6', glow2: '#ff6fae', accent: '#eaa8ff', onAccent: '#1e0827' },
  rose: { top: '#1d0a15', bottom: '#5c1439', glow: '#ff3d9a', glow2: '#ffb36b', accent: '#ff9fd0', onAccent: '#2a0617' },
  electric: { top: '#050c1d', bottom: '#0a2a5c', glow: '#2979ff', glow2: '#00e5d4', accent: '#4df3ff', onAccent: '#021a20' },
  steel: { top: '#0d0e11', bottom: '#2c3038', glow: '#8a94a6', glow2: '#e05a47', accent: '#f0f0f0', onAccent: '#121316' },
  court: { top: '#06112a', bottom: '#0b2f6b', glow: '#2f6bff', glow2: '#ff3b4a', accent: '#ff4d5a', onAccent: '#ffffff' },
}

type Block = { h: number; cut?: boolean; draw: (ctx: CanvasRenderingContext2D, y: number) => void }

function font(ctx: CanvasRenderingContext2D, size: number, weight: number, spacing = 0) {
  ctx.font = `${weight} ${size}px ${FONT}`
  if ('letterSpacing' in ctx) (ctx as unknown as { letterSpacing: string }).letterSpacing = `${spacing}px`
}

// Greedy word wrap; a single word wider than the line (long names, URLs) is broken by characters.
function wrap(ctx: CanvasRenderingContext2D, text: string, maxW: number): string[] {
  const lines: string[] = []
  for (const para of text.split('\n')) {
    let line = ''
    for (let word of para.split(/\s+/).filter(Boolean)) {
      while (ctx.measureText(word).width > maxW && word.length > 1) {
        let cut = word.length - 1
        while (cut > 1 && ctx.measureText(word.slice(0, cut)).width > maxW) cut--
        if (line) { lines.push(line); line = '' }
        lines.push(word.slice(0, cut))
        word = word.slice(cut)
      }
      const test = line ? `${line} ${word}` : word
      if (ctx.measureText(test).width <= maxW) line = test
      else { if (line) lines.push(line); line = word }
    }
    if (line) lines.push(line)
  }
  return lines
}

// Largest size from `size` down to `min` at which the text fits in maxLines; at min, the last line is cut with "…".
function fit(ctx: CanvasRenderingContext2D, text: string, maxW: number, maxLines: number, size: number, min: number,
             weight: number, spacing = 0): { lines: string[]; size: number } {
  const words = text.split(/\s+/).filter(Boolean)
  for (let s = size; s >= min; s -= 2) {
    font(ctx, s, weight, spacing)
    if (words.some((w) => ctx.measureText(w).width > maxW)) continue // shrink before splitting a word
    const lines = wrap(ctx, text, maxW)
    if (lines.length <= maxLines) return { lines, size: s }
  }
  // At the minimum size a word wider than the line (a long URL-like name) may be split; past maxLines, cut with "…"
  font(ctx, min, weight, spacing)
  const all = wrap(ctx, text, maxW)
  if (all.length <= maxLines) return { lines: all, size: min }
  const lines = all.slice(0, maxLines)
  let last = lines[maxLines - 1] ?? ''
  while (last && ctx.measureText(`${last}…`).width > maxW) last = last.slice(0, -1)
  lines[maxLines - 1] = `${last.trimEnd()}…`
  return { lines, size: min }
}

type TextOpts = { size: number; min: number; lines: number; weight: number; color: string; spacing?: number
  lineHeight?: number; align?: CanvasTextAlign; maxW?: number
  oneLineMin?: number } // shrink down to this size to keep it on one line before wrapping (sale times)

function textBlock(ctx: CanvasRenderingContext2D, text: string, o: TextOpts): Block | null {
  if (!text.trim()) return null
  const maxW = o.maxW ?? SAFE.right - SAFE.left
  let { lines, size } = fit(ctx, text, maxW, o.lines, o.size, o.min, o.weight, o.spacing)
  if (o.oneLineMin && lines.length > 1) {
    const one = fit(ctx, text, maxW, 1, o.size, o.oneLineMin, o.weight, o.spacing)
    if (!one.lines[0].endsWith('…')) ({ lines, size } = one)
  }
  const lh = Math.round(size * (o.lineHeight ?? 1.08))
  return {
    h: lh * lines.length,
    cut: lines[lines.length - 1].endsWith('…') && !text.trimEnd().endsWith('…'),
    draw: (c, y) => {
      font(c, size, o.weight, o.spacing)
      c.fillStyle = o.color
      c.textAlign = o.align ?? 'left'
      c.textBaseline = 'top'
      const x = o.align === 'center' ? (SAFE.left + SAFE.right) / 2 : SAFE.left
      lines.forEach((l, i) => c.fillText(l, x, y + i * lh))
    },
  }
}

function pillBlock(ctx: CanvasRenderingContext2D, text: string, size: number, p: Palette, align: CanvasTextAlign = 'left'): Block | null {
  if (!text.trim()) return null
  const { lines, size: s } = fit(ctx, text.toUpperCase(), SAFE.right - SAFE.left - 56, 1, size, Math.round(size * 0.6), 800, 4)
  font(ctx, s, 800, 4)
  const w = ctx.measureText(lines[0]).width + 56
  const h = Math.round(s * 1.0) + 34
  return {
    h,
    draw: (c, y) => {
      const x = align === 'center' ? (SAFE.left + SAFE.right - w) / 2 : SAFE.left
      c.fillStyle = p.accent
      c.beginPath()
      c.roundRect(x, y, w, h, h / 2)
      c.fill()
      font(c, s, 800, 4)
      c.fillStyle = p.onAccent
      c.textAlign = 'left'
      c.textBaseline = 'middle'
      c.fillText(lines[0], x + 28, y + h / 2 + 2)
    },
  }
}

const gap = (h: number): Block => ({ h, draw: () => {} })

function blocksFor(ctx: CanvasRenderingContext2D, slide: Slide, p: Palette, k: number, hasImage: boolean): Block[] {
  const f = slide.fields
  const big = hasImage ? 0.72 : 1
  const n = (v: number) => Math.round(v * k * big)
  const white = '#ffffff'
  const soft = 'rgba(255,255,255,0.86)'
  const out: (Block | null)[] = []
  if (slide.kind === 'title') {
    out.push(pillBlock(ctx, f.kicker ?? '', n(48), p), gap(n(44)),
      textBlock(ctx, f.headline ?? '', { size: n(200), min: n(80), lines: hasImage ? 3 : 4, weight: 900, color: white, lineHeight: 1.02 }),
      gap(n(36)),
      textBlock(ctx, f.sub ?? '', { size: n(72), min: n(40), lines: 3, weight: 600, color: soft }),
      gap(n(48)),
      textBlock(ctx, f.note ?? '', { size: n(30), min: 22, lines: 2, weight: 600, color: 'rgba(255,255,255,0.6)' }))
  } else if (slide.kind === 'show') {
    out.push(pillBlock(ctx, f.date ?? '', n(64), p), gap(n(24)),
      textBlock(ctx, f.time ?? '', { size: n(62), min: n(40), lines: 1, weight: 600, color: soft }), gap(n(48)),
      textBlock(ctx, f.artist ?? '', { size: n(190), min: n(72), lines: hasImage ? 3 : 4, weight: 900, color: white, lineHeight: 1.02 }),
      gap(n(40)),
      textBlock(ctx, f.venue ?? '', { size: n(66), min: n(40), lines: 2, weight: 600, color: soft }), gap(n(28)),
      textBlock(ctx, f.extra ?? '', { size: n(52), min: n(34), lines: 2, weight: 700, color: p.accent }))
  } else if (slide.kind === 'sales') {
    out.push(textBlock(ctx, f.heading ?? '', { size: n(112), min: n(64), lines: 2, weight: 900, color: white }), gap(n(50)))
    for (const raw of (f.lines ?? '').split('\n').filter((l) => l.trim())) {
      const at = raw.indexOf(': ')
      const label = at > 0 ? raw.slice(0, at) : ''
      const value = at > 0 ? raw.slice(at + 2) : raw
      if (label) out.push(textBlock(ctx, label, { size: n(42), min: n(30), lines: 2, weight: 700, color: p.accent }), gap(n(8)))
      out.push(textBlock(ctx, value, { size: n(64), min: n(40), lines: 2, weight: 800, color: white, oneLineMin: n(48) }), gap(n(40)))
    }
  } else {
    out.push(textBlock(ctx, f.headline ?? '', { size: n(150), min: n(76), lines: 4, weight: 900, color: white, align: 'center', lineHeight: 1.04 }),
      gap(n(44)),
      textBlock(ctx, f.sub ?? '', { size: n(60), min: n(40), lines: 3, weight: 600, color: soft, align: 'center' }))
  }
  return out.filter((b): b is Block => b !== null)
}

function background(ctx: CanvasRenderingContext2D, p: Palette) {
  const g = ctx.createLinearGradient(0, 0, 0, H)
  g.addColorStop(0, p.top)
  g.addColorStop(1, p.bottom)
  ctx.fillStyle = g
  ctx.fillRect(0, 0, W, H)
  for (const [x, y, r, color, a] of [[W * 0.88, H * 0.1, 760, p.glow, 0.55], [W * 0.05, H * 0.78, 680, p.glow2, 0.28]] as const) {
    const rg = ctx.createRadialGradient(x, y, 0, x, y, r)
    rg.addColorStop(0, hexA(color, a))
    rg.addColorStop(1, hexA(color, 0))
    ctx.fillStyle = rg
    ctx.fillRect(0, 0, W, H)
  }
}

function hexA(hex: string, a: number) {
  const v = parseInt(hex.slice(1), 16)
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`
}

const IMAGE_TOP = 200
const IMAGE_HEIGHTS = [820, 640, 480] // the photo gives way before long text gets tiny
const imageBottom = (h: number) => IMAGE_TOP + h + 60

function drawImage(ctx: CanvasRenderingContext2D, img: HTMLImageElement, credit: string, h: number) {
  const x = SAFE.left, y = IMAGE_TOP, w = W - 2 * SAFE.left
  const scale = Math.max(w / img.width, h / img.height)
  const sw = w / scale, sh = h / scale
  ctx.save()
  ctx.beginPath()
  ctx.roundRect(x, y, w, h, 36)
  ctx.clip()
  ctx.drawImage(img, (img.width - sw) / 2, (img.height - sh) / 2, sw, sh, x, y, w, h)
  ctx.restore()
  font(ctx, 26, 600)
  ctx.fillStyle = 'rgba(255,255,255,0.7)'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  const line = fit(ctx, `Photo: ${credit}`, w, 1, 26, 20, 600).lines[0]
  ctx.fillText(line, x, y + h + 14)
}

const images = new Map<string, Promise<HTMLImageElement>>()
export function loadImage(src: string): Promise<HTMLImageElement> {
  if (!images.has(src)) {
    images.set(src, new Promise((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('image failed to load'))
      img.src = src
    }))
  }
  return images.get(src)!
}

// Draw `slide` (position `index` of `total` visible slides) onto a 1080x1920 canvas.
export async function renderSlide(canvas: HTMLCanvasElement, m: SlideManifest, slide: Slide, index: number, total: number) {
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')!
  const p = PALETTES[m.theme] ?? PALETTES.night
  background(ctx, p)

  font(ctx, 32, 700, 6)
  ctx.fillStyle = 'rgba(255,255,255,0.72)'
  ctx.textBaseline = 'alphabetic'
  ctx.textAlign = 'left'
  ctx.fillText(fit(ctx, (m.brand || '').toUpperCase(), 640, 1, 32, 22, 700, 6).lines[0] ?? '', SAFE.left, 120)
  if (total > 1) {
    font(ctx, 30, 600)
    ctx.textAlign = 'right'
    ctx.fillText(`${index + 1} / ${total}`, W - SAFE.left, 120)
  }

  const img = slide.image ? await loadImage(slide.image.data_url).catch(() => null) : null
  // Shrink everything together until the content fits between `top` and the TikTok caption zone;
  // with a photo, try a shorter photo before settling for small text.
  let top = SAFE.top + 40
  let blocks: Block[] = []
  for (const imgH of img ? IMAGE_HEIGHTS : [0]) {
    top = img ? imageBottom(imgH) : SAFE.top + 40
    let k = 1
    for (; k >= 0.5; k -= 0.05) {  // smaller k also lowers each text's minimum size, so cut text gets room
      blocks = blocksFor(ctx, slide, p, k, !!img)
      if (blocks.reduce((s, b) => s + b.h, 0) <= SAFE.bottom - top && !blocks.some((b) => b.cut)) break
    }
    if (img && slide.image && (k >= 0.8 || imgH === IMAGE_HEIGHTS[IMAGE_HEIGHTS.length - 1])) {
      drawImage(ctx, img, slide.image.credit, imgH)
      break
    }
  }
  const room = SAFE.bottom - top
  const height = blocks.reduce((s, b) => s + b.h, 0)
  let y = img ? top : top + Math.max(0, (room - height) / 2 - 60)
  for (const b of blocks) {
    b.draw(ctx, y)
    y += b.h
  }
}

export function toBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('export failed'))), 'image/png'))
}
