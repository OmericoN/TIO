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

export function FactChart({ chart }: { chart: ChartSpec }) {
  const largest = Math.max(...chart.points.map(point => Math.abs(point.value)), 1e-9)
  const rowHeight = 30, labelWidth = 128, valueWidth = 72, width = 360
  const barSpace = width - labelWidth - valueWidth
  const height = chart.points.length * rowHeight
  return <svg viewBox={`0 0 ${width} ${height}`} role="img"
    aria-label={chart.points.map(point => `${point.label}: ${format(point.value, chart.unit)}`).join(', ')}>
    {chart.points.map((point, index) => {
      const y = index * rowHeight
      const bar = Math.max(2, (Math.abs(point.value) / largest) * barSpace)
      return <g key={point.label + index} className={point.value < 0 ? 'negative' : undefined}>
        <text x={labelWidth - 8} y={y + rowHeight / 2} textAnchor="end" dominantBaseline="middle" className="chart-label">
          {point.label.length > 18 ? point.label.slice(0, 17) + '…' : point.label}</text>
        <rect x={labelWidth} y={y + 6} width={bar} height={rowHeight - 12} rx="3" className="chart-bar"
          style={{ animationDelay: `${index * 90}ms` }} />
        <text x={labelWidth + bar + 6} y={y + rowHeight / 2} dominantBaseline="middle" className="chart-value">
          {format(point.value, chart.unit)}</text>
      </g>
    })}
  </svg>
}
