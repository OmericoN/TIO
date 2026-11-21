// What the realtime panel shows while a line plays: a chart, a flow chart, a map or the story's own article photo.
// The producer picks the visual and the backend checks it against the story's fact cards before it gets here.
import { useEffect, useState } from 'react'
import { FactChart } from './FactChart'
import type { ChartSpec } from './FactChart'

type Place = { name: string; latitude: number; longitude: number }

export type Visual = {
  kind: 'chart' | 'flow' | 'map' | 'picture'
  title: string
  sources: string[]
  chart?: ChartSpec
  steps?: string[]
  places?: Place[]
  picture?: { url: string; caption: string }
}

function Flow({ steps }: { steps: string[] }) {
  return <ol className="visual-flow">
    {steps.map((step, index) => <li key={index} style={{ animationDelay: `${index * 120}ms` }}>{step}</li>)}
  </ol>
}

// A world map zoomed to the places, drawn with d3-geo from Natural Earth outlines (loaded only when a map shows).
function PlaceMap({ places }: { places: Place[] }) {
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
  return <svg viewBox="0 0 360 220" role="img" className="visual-map"
    aria-label={'Map: ' + places.map(p => p.name).join(', ')}>
    <rect width="360" height="220" className="map-sea" />
    {drawing && <>
      <path d={drawing.land} className="map-land" />
      <path d={drawing.borders} className="map-borders" />
      {drawing.pins.map(([x, y], index) => <g key={index} transform={`translate(${x} ${y})`} className="map-pin">
        <circle r="5" /><text x="8" y="4">{places[index].name}</text>
      </g>)}
    </>}
  </svg>
}

export function VisualPanel({ visual, leaving = false }: { visual: Visual; leaving?: boolean }) {
  const [broken, setBroken] = useState(false)
  if (visual.kind === 'picture' && (broken || !visual.picture)) return null
  return <figure className="fact-chart" data-leaving={leaving || undefined} aria-label={visual.title}>
    <figcaption>{visual.title}</figcaption>
    {visual.kind === 'chart' && visual.chart && <FactChart chart={visual.chart} />}
    {visual.kind === 'flow' && visual.steps && <Flow steps={visual.steps} />}
    {visual.kind === 'map' && visual.places && <PlaceMap places={visual.places} />}
    {visual.kind === 'picture' && visual.picture && <img className="visual-photo" src={visual.picture.url}
      alt={visual.picture.caption} referrerPolicy="no-referrer" onError={() => setBroken(true)} />}
    <p className="chart-source">
      {visual.kind === 'picture' ? 'Photo' : 'Source'}: {visual.sources.join(', ') || 'story reporting'}
      {visual.kind === 'map' && ' · approximate locations'}
    </p>
  </figure>
}
