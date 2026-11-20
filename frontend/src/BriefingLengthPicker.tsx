import { useId, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent } from 'react'

const maxMinutes = 60
const ticks = Array.from({ length: 25 }, (_, index) => index)

export function BriefingLengthPicker() {
  const labelId = useId()
  const sliderRef = useRef<HTMLInputElement>(null)
  const [minutes, setMinutes] = useState(0)
  const progress = minutes / maxMinutes * 180

  function moveMarker(event: PointerEvent<HTMLDivElement>) {
    const dial = event.currentTarget.getBoundingClientRect()
    const x = event.clientX - (dial.left + dial.width / 2)
    const y = Math.max(0, dial.bottom - event.clientY)
    if (x === 0 && y === 0) return
    setMinutes(Math.round(Math.atan2(y, -x) / Math.PI * maxMinutes))
  }

  return (
    <fieldset className="briefing-length">
      <legend id={labelId}>Preferred briefing length <span className="muted">· Optional</span></legend>
      <div className="briefing-length-card">
        <div
          className="briefing-length-dial"
          style={{ '--dial-progress': `${progress}deg` } as CSSProperties}
          aria-hidden="true"
          onPointerDown={event => {
            if (!event.isPrimary || event.button !== 0) return
            event.currentTarget.setPointerCapture(event.pointerId)
            sliderRef.current?.focus({ preventScroll: true })
            moveMarker(event)
          }}
          onPointerMove={event => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) moveMarker(event)
          }}
        >
          <div className="briefing-length-arc" />
          {ticks.map(tick => (
            <span
              key={tick}
              className={tick % 6 === 0 ? 'briefing-length-tick major' : 'briefing-length-tick'}
              style={{ transform: `rotate(${tick * 7.5 - 90}deg)` }}
            />
          ))}
          <div className="briefing-length-marker" />
        </div>
        <input
          ref={sliderRef}
          name="briefing_length_minutes"
          className="sr-only briefing-length-slider"
          type="range"
          min={0}
          max={maxMinutes}
          value={minutes}
          aria-labelledby={labelId}
          aria-valuetext={minutes === 0 ? 'Use the default length' : `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`}
          onChange={event => setMinutes(Number(event.target.value))}
        />
      </div>
    </fieldset>
  )
}
