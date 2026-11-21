import type { CSSProperties } from 'react'

// Bars for figures from the story's fact cards. Every value was checked on the backend against the card it names,
// in one shared unit, so the chart only shows what the reporting says.

export type ChartSpec = {
  kind: 'bars' | 'change' | 'comparison'
  title: string
  unit: string
  points: { label: string; value: number; card_id: string }[]
}

function format(value: number, unit: string) {
  const number = value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  if (!unit) return number
  if (unit.startsWith('$')) return `$${number}${unit.slice(1)}`
  return unit === '%' ? `${number}%` : `${number} ${unit}`
}

// Drawn as HTML rather than a scaled SVG so the labels stay a readable size at any window shape: the bars stretch with
// the card's width and the rows share its height, getting thinner rather than shrinking the text.
export function FactChart({ chart, highlight, selected, onSelect }: {
  chart: ChartSpec; highlight?: number | null; selected?: number | null; onSelect?: (index: number) => void
}) {
  const largest = Math.max(...chart.points.map(point => Math.abs(point.value)), 1e-9)
  return <div className="chart-rows" role="group" style={{ '--rows': chart.points.length } as CSSProperties}
    aria-label={chart.points.map(point => `${point.label}: ${format(point.value, chart.unit)}`).join(', ')}>
    {chart.points.map((point, index) => {
      const state = index === selected ? 'selected' : index === highlight ? 'spoken' : undefined
      return <div key={point.label + index} className={['chart-row', point.value < 0 ? 'negative' : ''].join(' ')}
        data-state={state} role="button" tabIndex={0} aria-label={`Source for ${point.label}`} title={point.label}
        onClick={() => onSelect?.(index)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') onSelect?.(index) }}>
        <span className="chart-label">{point.label}</span>
        <span className="chart-track">
          <span className="chart-bar" style={{ '--share': Math.abs(point.value) / largest, animationDelay: `${index * 90}ms` } as CSSProperties} />
          <span className="chart-value">{format(point.value, chart.unit)}</span>
        </span>
      </div>
    })}
  </div>
}
