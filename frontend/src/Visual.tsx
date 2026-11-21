// What the realtime panel shows while a line plays: a chart, a flow chart, a map or the story's own article photo.
// The producer picks the visual and the backend checks it against the story's fact cards before it gets here.
//
// The card is interactive: the part being talked about lights up as the speaker mentions it, tapping a bar, step
// or pin shows the exact source quote behind it, and Expand opens it larger.
import { useEffect, useMemo, useRef, useState } from 'react'
import { FactChart } from './FactChart'
import type { ChartSpec } from './FactChart'

type Place = { name: string; latitude: number; longitude: number; card_id?: string }
type Evidence = Record<string, { point: string; quotes: { publisher: string; quote: string; url?: string | null }[] }>

export type Visual = {
  kind: 'chart' | 'flow' | 'map' | 'picture'
  title: string
  sources: string[]
  chart?: ChartSpec
  steps?: string[]
  step_cards?: string[]
  places?: Place[]
  picture?: { url: string; caption: string }
  evidence?: Evidence
}

const words = (text: string) => new Set((text.toLowerCase().match(/[a-z0-9.]{3,}/g) ?? [])
  .filter(word => !['the', 'and', 'for', 'that', 'this', 'with', 'from', 'was', 'are', 'its'].includes(word)))

// The element the speaker is talking about right now: the label sharing most words with their last sentence or so.
function spokenIndex(labels: string[], spoken: string) {
  const recent = words(spoken.slice(-160))
  let best = -1, score = 0
  labels.forEach((label, index) => {
    const shared = [...words(label)].filter(word => recent.has(word)).length
    if (shared > score) { best = index; score = shared }
  })
  return best >= 0 ? best : null
}

function Flow({ steps, highlight, selected, onSelect }: {
  steps: string[]; highlight: number | null; selected: number | null; onSelect: (index: number) => void
}) {
  return <ol className="visual-flow">
    {steps.map((step, index) => <li key={index} style={{ animationDelay: `${index * 120}ms` }}
      data-state={index === selected ? 'selected' : index === highlight ? 'spoken' : undefined}>
      <button type="button" onClick={() => onSelect(index)}>{step}</button>
    </li>)}
  </ol>
}

// A world map zoomed to the places, drawn with d3-geo from Natural Earth outlines (loaded only when a map shows).
function PlaceMap({ places, highlight, selected, onSelect }: {
  places: Place[]; highlight: number | null; selected: number | null; onSelect: (index: number) => void
}) {
  const [drawing, setDrawing] = useState<{ land: string; borders: string; pins: [number, number][] } | null>(null)
  useEffect(() => {
    let current = true
    void Promise.all([import('d3-geo'), import('topojson-client'), import('world-atlas/countries-110m.json')])
      .then(([geo, topojson, atlas]) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const world = ((atlas as any).default ?? atlas) as any
        const land = topojson.feature(world, world.objects.countries)
        const borders = topojson.mesh(world, world.objects.countries, (a: unknown, b: unknown) => a !== b)
        const lons = places.map(p => p.longitude), lats = places.map(p => p.latitude)
        const pad = Math.max(8, (Math.max(...lons) - Math.min(...lons)) * 0.4, (Math.max(...lats) - Math.min(...lats)) * 0.4)
        const area = { type: 'Feature' as const, properties: {}, geometry: { type: 'MultiPoint' as const, coordinates: [
          [Math.max(-180, Math.min(...lons) - pad), Math.max(-85, Math.min(...lats) - pad)],
          [Math.min(180, Math.max(...lons) + pad), Math.min(85, Math.max(...lats) + pad)]] } }
        const projection = geo.geoEqualEarth().fitExtent([[8, 8], [352, 212]], area)
        const path = geo.geoPath(projection)
        if (current) setDrawing({ land: path(land) ?? '', borders: path(borders) ?? '',
          pins: places.map(p => projection([p.longitude, p.latitude]) ?? [-99, -99]) })
      })
      .catch(() => { if (current) setDrawing({ land: '', borders: '', pins: [] }) })
    return () => { current = false }
  }, [places])
  return <svg viewBox="0 0 360 220" role="group" className="visual-map" aria-label={'Map: ' + places.map(p => p.name).join(', ')}>
    <rect width="360" height="220" className="map-sea" />
    {drawing && <>
      <path d={drawing.land} className="map-land" />
      <path d={drawing.borders} className="map-borders" />
      {drawing.pins.map(([x, y], index) => <g key={index} transform={`translate(${x} ${y})`} className="map-pin"
        data-state={index === selected ? 'selected' : index === highlight ? 'spoken' : undefined}
        role="button" tabIndex={0} aria-label={`Source for ${places[index].name}`}
        onClick={() => onSelect(index)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') onSelect(index) }}>
        <circle r="5" /><text x="8" y="4">{places[index].name}</text>
      </g>)}
    </>}
  </svg>
}

export function VisualPanel({ visual, spoken = '' }: { visual: Visual; spoken?: string }) {
  const [broken, setBroken] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [expanded, setExpanded] = useState(false)
  const labels = visual.kind === 'chart' ? visual.chart?.points.map(p => `${p.label} ${p.value}`) ?? []
    : visual.kind === 'flow' ? visual.steps ?? [] : visual.kind === 'map' ? visual.places?.map(p => p.name) ?? [] : []
  const highlight = useMemo(() => spokenIndex(labels, spoken), [labels.join('|'), spoken])
  const cardIds = visual.kind === 'chart' ? visual.chart?.points.map(p => p.card_id) ?? []
    : visual.kind === 'flow' ? visual.step_cards ?? [] : visual.places?.map(p => p.card_id ?? '') ?? []
  const source = selected !== null ? visual.evidence?.[cardIds[selected]] : undefined
  const select = (index: number) => setSelected(current => current === index ? null : index)

  useEffect(() => {
    if (!expanded) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(false) }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [expanded])

  if (visual.kind === 'picture' && (broken || !visual.picture)) return null
  return <>
    {expanded && <div className="visual-backdrop" onClick={() => setExpanded(false)} aria-hidden="true" />}
    <figure className="fact-chart" data-kind={visual.kind} data-expanded={expanded || undefined} aria-label={visual.title}>
      <div className="visual-head">
        <figcaption>{visual.title}</figcaption>
        <div className="visual-actions">
          <button type="button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? 'Close' : 'Expand'}</button>
        </div>
      </div>
      {visual.kind === 'chart' && visual.chart && <FactChart chart={visual.chart} highlight={highlight} selected={selected} onSelect={select} />}
      {visual.kind === 'flow' && visual.steps && <Flow steps={visual.steps} highlight={highlight} selected={selected} onSelect={select} />}
      {visual.kind === 'map' && visual.places && <PlaceMap places={visual.places} highlight={highlight} selected={selected} onSelect={select} />}
      {visual.kind === 'picture' && visual.picture && <img className="visual-photo" src={visual.picture.url}
        alt={visual.picture.caption} referrerPolicy="no-referrer" onError={() => setBroken(true)} />}
      {source && <div className="visual-source" role="status">
        <p className="visual-source-point">{source.point}</p>
        {source.quotes.slice(0, 2).map((quote, index) => <blockquote key={index}>
          “{quote.quote}” <cite>{quote.url ? <a href={quote.url} target="_blank" rel="noreferrer">{quote.publisher}</a> : quote.publisher}</cite>
        </blockquote>)}
      </div>}
      <p className="chart-source">
        {visual.kind === 'picture' ? 'Photo' : 'Source'}: {visual.sources.join(', ') || 'story reporting'}
        {visual.kind === 'map' && ' · approximate locations'}
        {visual.kind !== 'picture' && !source && ' · tap an item for its source'}
      </p>
    </figure>
  </>
}

export type ShownVisual = { visual: Visual; story: string }
export const visualKey = (visual: Visual) => visual.kind + ':' + visual.title

// Every visual of the briefing stays, one slide at a time: a new one slides up from below, pushing the last one up,
// and scrolling or swiping back up (or the dots beside it) brings earlier ones back. Only the visual for the line
// being spoken lights up what the speaker mentions.
export function VisualStack({ visuals, live, spoken }: { visuals: ShownVisual[]; live: string; spoken: string }) {
  const track = useRef<HTMLDivElement>(null)
  const [shown, setShown] = useState(0)
  const labelled = new Set(visuals.map(item => item.story)).size > 1
  const slides = () => [...track.current?.children ?? []] as HTMLElement[]

  function slideTo(index: number) {
    const slide = slides()[index]
    if (!slide) return
    track.current!.scrollTo({ top: slide.offsetTop, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  }

  useEffect(() => {
    const index = visuals.findIndex(item => visualKey(item.visual) === live)
    if (index >= 0) slideTo(index)
  }, [live, visuals.length])

  function follow() {
    const top = track.current!.scrollTop
    const distances = slides().map(slide => Math.abs(slide.offsetTop - top))
    setShown(distances.indexOf(Math.min(...distances)))
  }

  if (!visuals.length) return null
  return <section className="visual-stack" aria-label="Visuals">
    <div className="visual-track" ref={track} onScroll={follow}>
      {visuals.map(({ visual, story }, index) => <div className="visual-item" key={visualKey(visual)}>
        {labelled && story !== visuals[index - 1]?.story && <p className="visual-story">{story}</p>}
        <VisualPanel visual={visual} spoken={visualKey(visual) === live ? spoken : ''} />
      </div>)}
    </div>
    {visuals.length > 1 && <nav className="visual-pager" aria-label="Choose a visual">
      {visuals.map(({ visual }, index) => <button type="button" key={visualKey(visual)} aria-label={'Show ' + visual.title}
        aria-current={index === shown || undefined} onClick={() => slideTo(index)} />)}
    </nav>}
  </section>
}
