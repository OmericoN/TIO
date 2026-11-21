import { useEffect, useRef } from 'react'

const listeningScale = .52
const barMotion = [
  { peak: 1.46, attack: 62 },
  { peak: 1.94, attack: 32 },
  { peak: 1.76, attack: 46 },
  { peak: 1.35, attack: 76 },
]

type Props = {
  microphone: MediaStream | null
  context: AudioContext | null
  recording: boolean
  disabled: boolean
  onToggle: () => void
}

export function TalkDeck({ microphone, context, recording, disabled, onToggle }: Props) {
  const waveform = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!recording || !microphone || !context || context.state === 'closed') return
    const motion = matchMedia('(prefers-reduced-motion: reduce)')
    const bars = Array.from(waveform.current!.querySelectorAll<HTMLElement>('span'))
    const source = context.createMediaStreamSource(microphone)
    const analyser = context.createAnalyser()
    const samples = new Float32Array(1024)
    analyser.fftSize = samples.length
    source.connect(analyser)
    const levels = bars.map(() => 1)
    let frame = 0
    let previousFrame = 0

    function measure(now: number) {
      const elapsed = Math.max(0, Math.min(now - previousFrame, 48))
      previousFrame = now
      analyser.getFloatTimeDomainData(samples)
      let power = 0
      for (const sample of samples) power += sample * sample
      const amplitude = Math.max(0, Math.sqrt(power / samples.length) - .01)
      const energy = Math.min(1, Math.sqrt(amplitude * 8))

      bars.forEach((bar, index) => {
        const { peak, attack } = barMotion[index]
        const target = listeningScale + energy * (peak - listeningScale)
        const time = target > levels[index] ? attack : attack + 105
        levels[index] += (target - levels[index]) * (1 - Math.exp(-elapsed / time))
        bar.style.transform = `scaleY(${levels[index].toFixed(3)})`
      })
      frame = requestAnimationFrame(measure)
    }

    function sync() {
      cancelAnimationFrame(frame)
      bars.forEach(bar => bar.style.removeProperty('transform'))
      previousFrame = performance.now()
      if (!motion.matches && !document.hidden) frame = requestAnimationFrame(measure)
    }

    sync()
    motion.addEventListener('change', sync)
    document.addEventListener('visibilitychange', sync)
    return () => {
      cancelAnimationFrame(frame)
      motion.removeEventListener('change', sync)
      document.removeEventListener('visibilitychange', sync)
      bars.forEach(bar => bar.style.removeProperty('transform'))
      source.disconnect()
      analyser.disconnect()
    }
  }, [context, microphone, recording])

  return <button type="button" className="talk-deck" aria-label="Interrupt with microphone" aria-pressed={recording}
    title={recording ? 'Send your question' : 'Interrupt with microphone'} disabled={disabled} onClick={onToggle}>
    <span className="talk-waveform" ref={waveform} aria-hidden="true"><span /><span /><span /><span /></span>
  </button>
}
