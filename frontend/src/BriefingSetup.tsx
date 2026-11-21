import { useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'

function ArcSlider({ label, value, min, max, lower = false, disabled, onChange }: {
  label: string; value: number; min: number; max: number; lower?: boolean; disabled: boolean; onChange: (value: number) => void
}) {
  const [dragging, setDragging] = useState(false)
  const start = lower ? 170 : -170, sweep = lower ? -160 : 160
  function point(step: number, radius: number) {
    const angle = (start + sweep * (step - min) / (max - min)) * Math.PI / 180
    return { x: 140 + radius * Math.cos(angle), y: 140 + radius * Math.sin(angle) }
  }
  function move(event: PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    const x = (event.clientX - rect.left) * 280 / rect.width - 140
    const y = (event.clientY - rect.top) * 140 / rect.height + (lower ? 0 : -140)
    const angle = Math.atan2(lower ? Math.max(0, y) : Math.min(0, y), x) * 180 / Math.PI
    const fraction = Math.max(0, Math.min(1, (angle - start) / sweep))
    onChange(min + Math.round(fraction * (max - min)))
  }
  function key(event: KeyboardEvent<SVGSVGElement>) {
    const next = { ArrowRight: value + 1, ArrowUp: value + 1, ArrowLeft: value - 1, ArrowDown: value - 1,
      PageUp: value + 3, PageDown: value - 3, Home: min, End: max }[event.key]
    if (next === undefined || disabled) return
    event.preventDefault()
    onChange(Math.max(min, Math.min(max, next)))
  }
  const first = point(min, 116), last = point(max, 116), edge = point(value, 116)
  const unit = lower ? 'experts' : 'minutes'
  return <div className={'setup-arc ' + (lower ? 'setup-arc-lower' : 'setup-arc-upper')} data-disabled={disabled}>
    <p className="dial-value" aria-hidden="true"><span>{label}</span><strong>{value} {lower ? 'experts' : 'min'}</strong></p>
    <svg viewBox={lower ? '0 140 280 140' : '0 0 280 140'} role="slider" tabIndex={disabled ? -1 : 0}
      aria-label={label} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} aria-valuetext={`${value} ${unit}`}
      aria-disabled={disabled} data-dragging={dragging} onKeyDown={key}
      onPointerDown={event => {
        if (disabled || !event.isPrimary || event.button !== 0) return
        event.preventDefault()
        event.currentTarget.focus()
        event.currentTarget.setPointerCapture(event.pointerId)
        setDragging(true)
        move(event)
      }} onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) move(event) }}
      onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
      onLostPointerCapture={() => setDragging(false)}>
      <path className="dial-track" d={`M${first.x},${first.y} A116,116 0 0,${lower ? 0 : 1} ${last.x},${last.y}`} />
      <path className="dial-progress" d={value > min ? `M${first.x},${first.y} A116,116 0 0,${lower ? 0 : 1} ${edge.x},${edge.y}` : undefined} />
      {Array.from({ length: max - min + 1 }, (_, index) => {
        const step = min + index, selected = step === value, a = point(step, selected ? 100 : 105), b = point(step, selected ? 131 : 125)
        return <line key={step} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="dial-tick" data-selected={step === value} data-passed={step <= value} />
      })}
    </svg>
  </div>
}

export function BriefingSetup({ minutes, experts, disabled, onMinutes, onExperts }: {
  minutes: number; experts: number; disabled: boolean
  onMinutes: (value: number) => void; onExperts: (value: number) => void
}) {
  return <div className="setup-dials" role="group" aria-label="Briefing setup">
    <ArcSlider label="Duration" value={minutes} min={1} max={15} disabled={disabled} onChange={onMinutes} />
    <ArcSlider label="Expert participants" value={experts} min={2} max={4} lower disabled={disabled} onChange={onExperts} />
  </div>
}
