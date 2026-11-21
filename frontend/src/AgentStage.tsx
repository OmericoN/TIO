import { useEffect, useLayoutEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { Orb } from './Orb'

type Props = {
  origin: { x: number; y: number; context: AudioContext | null }
  host: string
  guests: string[]
  speaker: string
  speech: MediaStream | null
  onConfirm?: () => void
  confirming?: boolean
  orbPhase: 'setup' | 'loading' | 'ready'
  readyFrom: { x: number; y: number } | null
  children?: ReactNode
}

export function AgentStage({ origin, host, guests, speaker, speech, onConfirm, confirming, orbPhase, readyFrom, children }: Props) {
  const stage = useRef<HTMLDivElement>(null)
  const hostBody = useRef<HTMLDivElement>(null)
  const entrance = useRef<Animation | null>(null)
  const hostLabel = /^(t[ií]o|main host)$/i.test(host.trim()) ? 'Tio' : host
  useLayoutEffect(() => {
    // The parent opens the native dialog in its layout effect.
    const frame = requestAnimationFrame(() => {
      if (matchMedia('(prefers-reduced-motion: reduce)').matches || !hostBody.current) return
      const rect = hostBody.current.getBoundingClientRect()
      const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2
      const dx = origin.x - x, dy = origin.y - y
      entrance.current = hostBody.current.animate([
        { transform: `translate(${dx}px,${dy}px) scale(${56 / rect.width})`, easing: 'cubic-bezier(.77,0,.175,1)' },
        { transform: `translate(${dx * .08}px,${dy * .06}px) scale(.98,1.08) rotate(4deg)`, offset: .6, easing: 'cubic-bezier(.23,1,.32,1)' },
        { transform: 'translateY(-12px) scale(1.1,.94) rotate(-2deg)', offset: .78, easing: 'cubic-bezier(.23,1,.32,1)' },
        { transform: 'translateY(4px) scale(.985,1.03) rotate(1deg)', offset: .9, easing: 'cubic-bezier(.23,1,.32,1)' },
        { transform: 'none' },
      ], { duration: 1600, easing: 'linear', fill: 'backwards' })
    })
    return () => { cancelAnimationFrame(frame); entrance.current?.cancel() }
  }, [origin])

  useLayoutEffect(() => {
    const body = hostBody.current
    if (orbPhase !== 'ready' || !readyFrom || !body || matchMedia('(prefers-reduced-motion: reduce)').matches) return
    entrance.current?.cancel()
    const rect = body.getBoundingClientRect()
    const dx = readyFrom.x - rect.left - rect.width / 2
    const dy = readyFrom.y - rect.top - rect.height / 2
    const settle = body.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }],
      { duration: 480, easing: 'cubic-bezier(.23, 1, .32, 1)' })
    return () => settle.cancel()
  }, [orbPhase, readyFrom])

  useLayoutEffect(() => {
    const body = hostBody.current
    const room = stage.current
    if (orbPhase !== 'ready' || !body || !room?.querySelector('.guest:not([data-born])')) return
    const reduced = matchMedia('(prefers-reduced-motion: reduce)')
    let cancelled = false

    function spawnGuests() {
      if (cancelled || !body || !room) return
      const hostOrb = body.querySelector('.orb-meter')!.getBoundingClientRect()
      const arrivals = room.querySelectorAll<HTMLElement>('.guest:not([data-born])')
      arrivals.forEach((guest, index) => {
        guest.dataset.born = 'true'
        if (reduced.matches) return
        const bud = guest.querySelector<HTMLElement>('.guest-bud')!
        const slot = bud.getBoundingClientRect()
        const dx = slot.left + slot.width / 2 - hostOrb.left - hostOrb.width / 2
        const dy = slot.top + slot.height / 2 - hostOrb.top - hostOrb.height / 2
        const rim = hostOrb.width / 2 + slot.width * .1
        const travel = 1 - Math.min(1, rim / Math.max(1, Math.hypot(dx, dy)))
        const angle = Math.atan2(dy, dx)
        const pose = (remaining: number, length: number, breadth: number) =>
          `translate(${-dx * travel * remaining}px, ${-dy * travel * remaining}px) rotate(${angle}rad) scale(${length}, ${breadth}) rotate(${-angle}rad)`
        const delay = index * 70

        bud.animate([
          { transform: pose(1, .28, .22), opacity: 0 },
          { transform: pose(.83, .53, .34), opacity: 1, offset: .25 },
          { transform: pose(.18, .96, .82), opacity: 1, offset: .7 },
          { transform: 'none', opacity: 1 },
        ], { duration: 460, delay, fill: 'backwards', easing: 'cubic-bezier(.23, 1, .32, 1)' })
        guest.querySelector(':scope > span')?.animate([{ opacity: 0 }, { opacity: 1 }],
          { duration: 160, delay: delay + 300, fill: 'backwards' })
      })
    }

    const morph = [...body.querySelectorAll('.host-glass, .host-core, .host-ring')]
      .flatMap(layer => layer.getAnimations())
    const handoff = [...body.getAnimations(), ...morph]
      .filter(animation => Number.isFinite(animation.effect?.getComputedTiming().endTime))
    if (reduced.matches || !handoff.length) spawnGuests()
    else void Promise.allSettled(handoff.map(animation => animation.finished)).then(spawnGuests)
    return () => { cancelled = true }
  }, [orbPhase, guests])

  useEffect(() => {
    const context = origin.context
    const actor = stage.current?.querySelector<HTMLElement>('.actor[data-speaking="true"]')
    const orb = actor?.querySelector<HTMLElement>('.orb-breath')
    if (orbPhase !== 'ready' || !actor || !orb || !context || context.state === 'closed' || !speech?.getAudioTracks().length) return

    const source = context.createMediaStreamSource(speech)
    const analyser = context.createAnalyser()
    const samples = new Float32Array(1024)
    analyser.fftSize = samples.length
    source.connect(analyser)
    const reduced = matchMedia('(prefers-reduced-motion: reduce)')
    let frame = 0, previous = 0, level = 0

    function measure(now: number) {
      analyser.getFloatTimeDomainData(samples)
      let power = 0
      for (const sample of samples) power += sample * sample
      const energy = Math.min(1, Math.sqrt(Math.max(0, Math.sqrt(power / samples.length) - .008) * 7))
      const elapsed = Math.max(0, Math.min(now - previous, 48))
      previous = now
      level += (energy - level) * (1 - Math.exp(-elapsed / (energy > level ? 35 : 125)))
      orb!.style.transform = `scale(${1 + level * .08}, ${1 + level * .17}) rotate(${level * 4}deg)`
      frame = requestAnimationFrame(measure)
    }

    function stop() {
      cancelAnimationFrame(frame)
      delete actor!.dataset.audioPulse
      orb!.style.removeProperty('transform')
    }
    function sync() {
      stop()
      if (reduced.matches || document.hidden || context!.state !== 'running') return
      actor!.dataset.audioPulse = 'true'
      previous = performance.now()
      level = 0
      frame = requestAnimationFrame(measure)
    }
    sync()
    reduced.addEventListener('change', sync)
    document.addEventListener('visibilitychange', sync)
    context.addEventListener('statechange', sync)
    return () => {
      stop()
      reduced.removeEventListener('change', sync)
      document.removeEventListener('visibilitychange', sync)
      context.removeEventListener('statechange', sync)
      source.disconnect()
      analyser.disconnect()
    }
  }, [origin.context, orbPhase, speaker, speech, guests])

  return <div className="agent-stage" data-phase={orbPhase} aria-label="Discussion participants" ref={stage}>
    {children}
    <div className="actor host" data-speaking={speaker === host}>
      <div className="host-body" ref={hostBody}>
        <div className="host-load" data-phase={orbPhase}>
          <div className="orb-meter">
            <div className="host-glass" aria-hidden="true" />
            <div className="host-core"><Orb /></div>
            <div className="host-ring" aria-hidden="true" />
            {onConfirm && <button type="button" className="host-confirm" aria-label="Begin briefing" disabled={confirming} onClick={onConfirm} />}
          </div>
        </div>
      </div>
      <span>{onConfirm ? 'Tap to begin' : hostLabel}</span>
    </div>
    {guests.map((guest, index) => <div key={guest} className={'actor guest guest-' + index} data-speaking={speaker === guest}>
      <div className="guest-bud"><div className="orb-meter"><Orb /></div></div><span>{guest}</span>
    </div>)}
  </div>
}
