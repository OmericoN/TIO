import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Orb } from './Orb'
import { Icon } from './Brand'

type Persona = { id: string; name: string; tagline: string; preview_url: string | null }

type Props = {
  selectedIds: string[]
  onToggle: (id: string) => void
  suspended?: boolean
}

function VoiceSample({ voice, muted, selected, suspended }: {
  voice: Persona; muted: boolean; selected: boolean; suspended: boolean
}) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const attempt = useRef(0)
  const [status, setStatus] = useState<'idle' | 'loading' | 'playing' | 'failed'>('idle')
  const url = suspended ? null : voice.preview_url
  const playSample = useCallback(async (replay = false) => {
    const audio = audioRef.current
    if (!audio || !url) return
    const current = ++attempt.current
    setStatus('loading')
    try {
      if (replay) audio.currentTime = 0
      await audio.play()
      if (attempt.current === current) setStatus('playing')
    } catch (error) {
      if (attempt.current !== current) return
      setStatus(error instanceof DOMException && error.name === 'NotAllowedError' ? 'idle' : 'failed')
    }
  }, [url])

  useEffect(() => {
    const audio = audioRef.current
    setStatus('idle')
    void playSample()
    return () => {
      attempt.current += 1
      audio?.pause()
    }
  }, [playSample])

  return <>
    {url && <button type="button" className="button voice-preview-play" onClick={() => void playSample(true)}>Play sample</button>}
    <p className="voice-preview-status muted">{!voice.preview_url ? 'No preview available · '
      : url && status === 'loading' ? 'Loading preview… · ' : url && status === 'playing' ? 'Previewing · ' : ''}
    {selected ? 'Tap the orb to remove' : 'Tap the orb to save'}</p>
    {url && status === 'failed' && <p role="alert" className="voice-preview-status text-red-700">Could not play the sample for {voice.name}.</p>}
    <audio ref={audioRef} src={url ?? undefined} preload="none" muted={muted}
      onEnded={() => setStatus('idle')} onError={() => { if (url) setStatus('failed') }} />
  </>
}

export function VoiceAuditions({ selectedIds, onToggle, suspended = false }: Props) {
  const [voices, setVoices] = useState<Persona[]>([])
  const [activeIndex, setActiveIndex] = useState(0)
  const [paletteOffset] = useState(() => Math.floor(Math.random() * 360))
  const [slide, setSlide] = useState<{ direction: -1 | 1; outgoingHue: number } | null>(null)
  const [catalogStatus, setCatalogStatus] = useState('Loading voices…')
  const [muted, setMuted] = useState(false)
  const drag = useRef<{ x: number; y: number } | null>(null)
  const swiped = useRef(false)

  useEffect(() => {
    const request = new AbortController()

    async function loadVoices() {
      try {
        const response = await fetch('/api/voice/catalog', { signal: request.signal })
        if (!response.ok) throw new Error()
        const entries: Persona[] = await response.json()
        if (!Array.isArray(entries) || entries.some(voice => !voice || typeof voice.id !== 'string' || !voice.id.trim()
          || typeof voice.name !== 'string' || typeof voice.tagline !== 'string'
          || (voice.preview_url !== null && (typeof voice.preview_url !== 'string'
            || !voice.preview_url.startsWith('/api/voice/previews/'))))) throw new Error()
        for (let index = entries.length - 1; index > 0; index -= 1) {
          const swapIndex = Math.floor(Math.random() * (index + 1))
          const entry = entries[index]
          entries[index] = entries[swapIndex]
          entries[swapIndex] = entry
        }
        setVoices(entries)
        setCatalogStatus(entries.length ? '' : 'No voices are available yet.')
      } catch {
        if (!request.signal.aborted) setCatalogStatus('Could not load voices. You can continue without choosing.')
      }
    }

    void loadVoices()
    return () => request.abort()
  }, [])

  const voice = voices[activeIndex]

  const selected = voice ? selectedIds.includes(voice.id) : false
  // A large hue step keeps neighboring voices visually distinct.
  const hueFor = (id: string) => (paletteOffset + voices.findIndex(voice => voice.id === id) * 137) % 360
  const hue = voice ? hueFor(voice.id) : paletteOffset
  const colorStyle = {
    '--voice-slide': `${(slide?.direction ?? 1) * 76}px`,
  } as CSSProperties

  function showVoice(index: number) {
    if (index === activeIndex) return
    setSlide({ direction: index > activeIndex ? 1 : -1, outgoingHue: hue })
    setActiveIndex(index)
  }

  return (
    <section className="voice-auditions" aria-labelledby="voice-auditions-title">
      <div className="voice-picker-heading">
        <h3 id="voice-auditions-title" tabIndex={-1}>Pick up to three favorite voices</h3>
        <button type="button" className="voice-mute" aria-label={muted ? 'Unmute voice previews' : 'Mute voice previews'}
          title={muted ? 'Unmute voice previews' : 'Mute voice previews'} aria-pressed={muted}
          onClick={() => setMuted(current => !current)}><Icon name={muted ? 'muted' : 'sound'} /></button>
      </div>
      <p role="status" className={catalogStatus ? 'muted mt-2' : 'sr-only'}>{catalogStatus}</p>
      {voice && (
        <div className="voice-carousel" role="group" aria-label="Voice preview" style={colorStyle}>
          <div className="voice-carousel-info" aria-live="polite">
            <h4>{voice.name}</h4>
            <p className="muted" title={voice.tagline || undefined}>{voice.tagline}</p>
          </div>
          <div className="voice-carousel-stage" onPointerDown={event => {
            if (!event.isPrimary || event.button !== 0 || (event.target as HTMLElement).closest('.voice-carousel-arrow')) return
            swiped.current = false
            drag.current = { x: event.clientX, y: event.clientY }
          }} onPointerMove={event => {
            if (drag.current && Math.abs(event.clientX - drag.current.x) > 12
              && Math.abs(event.clientX - drag.current.x) > Math.abs(event.clientY - drag.current.y)) event.currentTarget.setPointerCapture(event.pointerId)
          }} onPointerUp={event => {
            const from = drag.current
            drag.current = null
            if (!from) return
            const dx = event.clientX - from.x
            if (Math.abs(dx) >= 48 && Math.abs(dx) > 1.5 * Math.abs(event.clientY - from.y)) {
              swiped.current = true
              showVoice(Math.max(0, Math.min(voices.length - 1, activeIndex + (dx < 0 ? 1 : -1))))
            }
          }} onPointerCancel={() => { drag.current = null }} onLostPointerCapture={event => { if (event.target === event.currentTarget) drag.current = null }}>
            <button
              type="button"
              className="voice-carousel-arrow"
              aria-label="Previous voice"
              disabled={activeIndex === 0}
              onClick={() => showVoice(activeIndex - 1)}
            >
              <span aria-hidden="true">‹</span>
            </button>
            <div className="voice-carousel-orb">
              {slide && (
                <div
                  key={'out-' + voice.id}
                  className="voice-orb-layer voice-orb-exit"
                  style={{ '--orb-hue': slide.outgoingHue } as CSSProperties}
                  onAnimationEnd={() => setSlide(current => current === slide ? null : current)}
                >
                  <Orb />
                </div>
              )}
              <div
                key={voice.id}
                className={slide ? 'voice-orb-layer voice-orb-enter' : 'voice-orb-layer'}
                style={{ '--orb-hue': hue } as CSSProperties}
              >
                <button type="button" className="agent-launch voice-save-orb" aria-label={selected ? `Remove ${voice.name} from saved voices` : `Save ${voice.name}`}
                  aria-pressed={selected} onClick={event => { if (!swiped.current || event.detail === 0) onToggle(voice.id); swiped.current = false }}>
                  <Orb />{selected && <span className="voice-saved-check" aria-hidden="true">✓</span>}
                </button>
              </div>
            </div>
            <button
              type="button"
              className="voice-carousel-arrow"
              aria-label="Next voice"
              disabled={activeIndex === voices.length - 1}
              onClick={() => showVoice(activeIndex + 1)}
            >
              <span aria-hidden="true">›</span>
            </button>
          </div>
          <div className="voice-carousel-dots" role="group" aria-label="Nearby voices">
            {voices.map((dotVoice, index) => {
              const distance = index - activeIndex
              const nearby = Math.abs(distance) <= 3
              const position = Math.max(-4, Math.min(4, distance))
              return (
                <button
                  key={dotVoice.id}
                  type="button"
                  aria-label={dotVoice.name}
                  aria-current={distance === 0 ? 'true' : undefined}
                  aria-hidden={!nearby}
                  tabIndex={nearby ? 0 : -1}
                  style={{ '--dot-x': `${position * 25}px`, '--dot-y': `${-(position ** 2)}px` } as CSSProperties}
                  onClick={() => showVoice(index)}
                />
              )
            })}
          </div>
          <VoiceSample key={voice.id} voice={voice} muted={muted} selected={selected} suspended={suspended} />
        </div>
      )}
      <section className="saved-voices" aria-labelledby="saved-voices-title">
        <h4 id="saved-voices-title">Saved voices <span className="muted">{selectedIds.length}</span></h4>
        {!selectedIds.length && <p className="muted">Tap a voice orb to add it here.</p>}
        <ul>{selectedIds.map(id => {
          const saved = voices.find(voice => voice.id === id)
          return saved && <li key={id}><button type="button" className="saved-voice glass-card" aria-label={`Remove ${saved.name} from saved voices`} onClick={() => onToggle(id)}>
            <span className="saved-voice-orb" style={{ '--orb-hue': hueFor(id) } as CSSProperties} aria-hidden="true"><Orb /></span>
            <span>{saved.name}</span><span className="saved-voice-remove" aria-hidden="true">×</span>
          </button></li>
        })}</ul>
      </section>
    </section>
  )
}
