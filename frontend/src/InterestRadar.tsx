import { useId, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'

/** Topic -> 1 (somewhat) .. 3 (very interested). Topics at 0 are left out. */
export type InterestLevels = Record<string, number>
export type RadarTopic = { name: string; short: string }

export const maxLevel = 3
export const levelNames = ['Not interested', 'Somewhat interested', 'Interested', 'Very interested']
const ringNames = ['', 'Somewhat', 'Interested', 'Very']
const fontSize = 12
const labelGap = 10

const clamp = (value: number) => Math.max(0, Math.min(maxLevel, value))
// DM Sans averages a little over half an em per character; a slight overestimate keeps labels inside the box.
const labelWidth = (text: string) => text.length * fontSize * 0.6 + 4

/** Fit the largest radar whose spoke labels stay inside the measured width. */
function fit(width: number, topics: RadarTopic[]) {
  const step = 2 * Math.PI / topics.length
  let outer = 168
  topics.forEach((topic, index) => {
    const reach = Math.abs(Math.cos(-Math.PI / 2 + index * step))
    if (reach > 0.2) outer = Math.min(outer, (width / 2 - 4 - labelWidth(topic.short)) / reach - labelGap)
  })
  outer = Math.max(64, Math.floor(outer))
  const height = Math.round(2 * (outer + labelGap + fontSize + 12))
  return { step, outer, hole: Math.max(16, Math.round(outer * 0.18)), cx: width / 2, cy: height / 2, height }
}

/**
 * A radar of how strongly the listener follows each topic. Each spoke is a slider: the centre is "not interested"
 * and each ring out is one level more. Tap or drag along a spoke to set it; tap a label to switch the topic between
 * off and very interested. Each handle takes arrow keys, Home, End and 0–3.
 */
export function InterestRadar({ topics, levels, onChange, labelledBy }: {
  topics: RadarTopic[]
  levels: InterestLevels
  onChange: (levels: InterestLevels) => void
  labelledBy?: string
}) {
  const hintId = useId()
  const wrap = useRef<HTMLDivElement>(null)
  const svg = useRef<SVGSVGElement>(null)
  const handles = useRef<(SVGCircleElement | null)[]>([])
  const drag = useRef<{ pointerId: number; index: number; x: number; y: number; moved: boolean; onLabel: boolean } | null>(null)
  const [width, setWidth] = useState(440)
  const [hover, setHover] = useState<{ index: number; level: number } | null>(null)
  const [focused, setFocused] = useState<number | null>(null)

  useLayoutEffect(() => {
    const element = wrap.current
    if (!element) return
    const measure = () => setWidth(Math.max(260, Math.round(element.clientWidth)))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const { step, outer, hole, cx, cy, height } = fit(width, topics)
  const angle = (index: number) => -Math.PI / 2 + index * step
  const radius = (level: number) => hole + (outer - hole) * level / maxLevel
  const at = (index: number, r: number) => [cx + r * Math.cos(angle(index)), cy + r * Math.sin(angle(index))] as const
  const levelOf = (index: number) => levels[topics[index].name] ?? 0
  const active = drag.current?.index ?? focused ?? hover?.index ?? null

  function set(index: number, level: number) {
    const name = topics[index].name
    if ((levels[name] ?? 0) === level) return
    const next: InterestLevels = {}
    for (const topic of topics) {
      const value = topic.name === name ? level : levels[topic.name]
      if (value) next[topic.name] = value
    }
    onChange(next)
  }

  // Pointer position in chart units: the nearest spoke, and how far out along it.
  function locate(event: ReactPointerEvent, lockedIndex?: number) {
    const box = svg.current!.getBoundingClientRect()
    const x = (event.clientX - box.left) * width / box.width - cx
    const y = (event.clientY - box.top) * height / box.height - cy
    const index = lockedIndex ?? (Math.round((Math.atan2(y, x) + Math.PI / 2) / step) % topics.length + topics.length) % topics.length
    const along = x * Math.cos(angle(index)) + y * Math.sin(angle(index))
    return { index, level: clamp(Math.round((along - hole) / (outer - hole) * maxLevel)), onLabel: along > outer + 8 }
  }

  function pointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.button !== 0) return
    const hit = locate(event)
    drag.current = { pointerId: event.pointerId, index: hit.index, x: event.clientX, y: event.clientY, moved: false, onLabel: hit.onLabel }
    event.currentTarget.setPointerCapture(event.pointerId)
    if (!hit.onLabel) set(hit.index, hit.level)
    setHover(hit)
    handles.current[hit.index]?.focus({ preventScroll: true })
  }

  function pointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const current = drag.current
    if (!current) {
      if (event.pointerType === 'mouse') setHover(locate(event))
      return
    }
    if (current.pointerId !== event.pointerId) return
    if (!current.moved && Math.hypot(event.clientX - current.x, event.clientY - current.y) > 4) current.moved = true
    if (!current.moved) return
    const hit = locate(event, current.index)
    set(hit.index, hit.level)
    setHover(hit)
  }

  function pointerUp(event: ReactPointerEvent<SVGSVGElement>) {
    const current = drag.current
    if (!current || current.pointerId !== event.pointerId) return
    drag.current = null
    if (current.onLabel && !current.moved) set(current.index, levelOf(current.index) === maxLevel ? 0 : maxLevel)
    if (event.pointerType !== 'mouse') setHover(null)
  }

  function keyDown(event: KeyboardEvent, index: number) {
    const now = levelOf(index)
    const next = ({ ArrowUp: now + 1, ArrowRight: now + 1, PageUp: now + 1, ArrowDown: now - 1, ArrowLeft: now - 1,
      PageDown: now - 1, Home: 0, End: maxLevel } as Record<string, number>)[event.key]
      ?? (/^[0-3]$/.test(event.key) ? Number(event.key) : undefined)
    if (next === undefined) return
    event.preventDefault()
    set(index, clamp(next))
  }

  function wedge(index: number) {
    const point = (a: number, r: number) => `${cx + r * Math.cos(a)} ${cy + r * Math.sin(a)}`
    const from = angle(index) - step / 2, to = angle(index) + step / 2
    return `M ${point(from, hole)} L ${point(from, outer)} A ${outer} ${outer} 0 0 1 ${point(to, outer)} `
      + `L ${point(to, hole)} A ${hole} ${hole} 0 0 0 ${point(from, hole)} Z`
  }

  const shape = topics.map((_, index) => at(index, radius(levelOf(index))).join(',')).join(' ')
  const ghost = hover && !drag.current && hover.index === active && !(hover.level === levelOf(hover.index))
    ? at(hover.index, radius(hover.level)) : null

  return <div ref={wrap} className="interest-radar">
    <svg ref={svg} viewBox={`0 0 ${width} ${height}`} role="group" aria-labelledby={labelledBy} aria-describedby={hintId}
      onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
      onPointerCancel={() => { drag.current = null; setHover(null) }} onPointerLeave={() => { if (!drag.current) setHover(null) }}>
      <g className="radar-grid" aria-hidden="true">
        {active !== null && <path className="radar-wedge" d={wedge(active)} />}
        {[1, 2, 3].map(level => <circle key={level} cx={cx} cy={cy} r={radius(level)} />)}
        <circle className="radar-hole" cx={cx} cy={cy} r={hole} />
        {topics.map((topic, index) => {
          const [x1, y1] = at(index, hole), [x2, y2] = at(index, outer)
          return <line key={topic.name} x1={x1} y1={y1} x2={x2} y2={y2} />
        })}
        {[1, 2, 3].map(level => <text key={level} className="radar-ring-label" x={cx - 4} y={cy - radius(level) + 11}
          textAnchor="end">{ringNames[level]}</text>)}
      </g>
      <polygon className="radar-shape" points={shape} aria-hidden="true" />
      {ghost && <circle className="radar-ghost" cx={ghost[0]} cy={ghost[1]} r={5} aria-hidden="true" />}
      {topics.map((topic, index) => {
        const level = levelOf(index)
        const [x, y] = at(index, radius(level))
        const cos = Math.cos(angle(index)), sin = Math.sin(angle(index))
        const [lx, ly] = at(index, outer + labelGap)
        return <g key={topic.name}>
          {/* Side labels grow away from the spoke; the top and bottom ones sit further out, clear of their neighbours. */}
          <text className="radar-label" x={lx} y={ly + sin * fontSize * (Math.abs(cos) < 0.1 ? 0.9 : 0.45)} data-level={level}
            data-active={active === index} textAnchor={cos > 0.1 ? 'start' : cos < -0.1 ? 'end' : 'middle'}
            dominantBaseline="central" aria-hidden="true">
            {topic.short}
          </text>
          <circle ref={element => { handles.current[index] = element }} className="radar-handle" data-level={level}
            cx={x} cy={y} r={level ? 5 : active === index ? 3.5 : 2.5} role="slider" tabIndex={0} aria-label={topic.name}
            aria-valuemin={0} aria-valuemax={maxLevel} aria-valuenow={level} aria-valuetext={levelNames[level]}
            onKeyDown={event => keyDown(event, index)} onFocus={() => setFocused(index)} onBlur={() => setFocused(null)} />
        </g>
      })}
    </svg>
    <p id={hintId} className="radar-readout">
      {active !== null
        ? <><strong>{topics[active].name}</strong> · {levelNames[levelOf(active)]}</>
        : 'Tap or drag along a spoke. The further out, the more of it in your briefing.'}
    </p>
  </div>
}
