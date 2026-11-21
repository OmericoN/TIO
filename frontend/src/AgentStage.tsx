import { useEffect, useLayoutEffect, useRef } from 'react'
import type { ReactNode, RefObject } from 'react'
import { Orb } from './Orb'

type Props = {
  origin: { x: number; y: number; context: AudioContext | null }
  audio: RefObject<HTMLAudioElement | null>
  host: string
  guests: string[]
  speaker: string
  onConfirm?: () => void
  confirming?: boolean
  children?: ReactNode
}

export function AgentStage({ origin, audio, host, guests, speaker, onConfirm, confirming, children }: Props) {
  const stage = useRef<HTMLDivElement>(null)
  const hostBody = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    let entrance: Animation | undefined
    // The parent opens the native dialog in its layout effect.
    const frame = requestAnimationFrame(() => {
      if (matchMedia('(prefers-reduced-motion: reduce)').matches || !hostBody.current) return
      const rect = hostBody.current.getBoundingClientRect()
      const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2
      const dx = origin.x - x, dy = origin.y - y
      entrance = hostBody.current.animate([
        { transform: `translate(${dx}px,${dy}px) scale(${56 / rect.width})`, easing: 'cubic-bezier(.77,0,.175,1)' },
        { transform: `translate(${dx * .08}px,${dy * .06}px) scale(.98,1.08) rotate(4deg)`, offset: .6, easing: 'cubic-bezier(.23,1,.32,1)' },
        { transform: 'translateY(-12px) scale(1.1,.94) rotate(-2deg)', offset: .78, easing: 'cubic-bezier(.23,1,.32,1)' },
        { transform: 'translateY(4px) scale(.985,1.03) rotate(1deg)', offset: .9, easing: 'cubic-bezier(.23,1,.32,1)' },
        { transform: 'none' },
      ], { duration: 1600, easing: 'linear', fill: 'backwards' })
    })
    return () => { cancelAnimationFrame(frame); entrance?.cancel() }
  }, [origin])

  useEffect(() => {
    const media = audio.current, ctx = origin.context
    if (!media || !ctx) return
    const reduced = matchMedia('(prefers-reduced-motion: reduce)')
    let analyser: AnalyserNode | undefined
    let source: MediaElementAudioSourceNode | undefined
    let frame = 0, level = 0
    let body: HTMLElement | null = null
    const samples = new Uint8Array(512)
    function stop() {
      cancelAnimationFrame(frame)
      body?.style.removeProperty('transform')
      level = 0
    }
    function measure() {
      if (!analyser || !media || media.paused || document.hidden || reduced.matches) { stop(); return }
      analyser.getByteTimeDomainData(samples)
      let power = 0
      for (const value of samples) power += ((value - 128) / 128) ** 2
      const amplitude = Math.min(1, Math.sqrt(power / samples.length) * 4)
      level += (amplitude - level) * (amplitude > level ? .48 : .14)
      const current = stage.current?.querySelector<HTMLElement>('[data-speaking="true"] .orb-meter') ?? null
      if (body !== current) body?.style.removeProperty('transform')
      body = current
      if (body) body.style.transform = `scale(${1 + level * .14},${1 + level * .21}) rotate(${level * 5}deg)`
      frame = requestAnimationFrame(measure)
    }
    function sync() {
      stop()
      if (!ctx || ctx.state !== 'running' || !media || reduced.matches) return
      if (!source) {
        try {
          analyser = ctx.createAnalyser()
          analyser.fftSize = samples.length
          source = ctx.createMediaElementSource(media)
          source.connect(analyser)
          analyser.connect(ctx.destination)
        } catch { return }
      }
      if (!media.paused && !document.hidden) measure()
    }
    sync()
    media.addEventListener('playing', sync)
    media.addEventListener('pause', stop)
    reduced.addEventListener('change', sync)
    ctx.addEventListener('statechange', sync)
    document.addEventListener('visibilitychange', sync)
    return () => {
      stop()
      media.removeEventListener('playing', sync)
      media.removeEventListener('pause', stop)
      reduced.removeEventListener('change', sync)
      ctx.removeEventListener('statechange', sync)
      document.removeEventListener('visibilitychange', sync)
      source?.disconnect()
      analyser?.disconnect()
    }
  }, [audio, origin])

  return <div ref={stage} className="agent-stage" aria-label="Discussion participants">
    {children}
    <div className="actor host" data-speaking={speaker === host}><div className="host-body" ref={hostBody}><div className="orb-meter">
      {onConfirm ? <button type="button" className="agent-launch host-confirm" aria-label="Begin briefing" disabled={confirming} onClick={onConfirm}><Orb /></button> : <Orb />}
    </div></div><span>{onConfirm ? confirming ? 'Starting…' : 'Tap to begin' : host}</span></div>
    {guests.map((guest, index) => <div key={guest} className={'actor guest guest-' + index} data-speaking={speaker === guest}><div className="orb-meter"><Orb /></div><span>{guest}</span></div>)}
  </div>
}
