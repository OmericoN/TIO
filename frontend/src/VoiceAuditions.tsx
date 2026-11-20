import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Orb } from './Orb'

type Persona = { id: string; name: string; tagline: string; can_preview: boolean }

type Props = {
  selectedIds: string[]
  onToggle: (id: string) => void
}

export function VoiceAuditions({ selectedIds, onToggle }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const previewRequest = useRef<AbortController | null>(null)
  const [voices, setVoices] = useState<Persona[]>([])
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [paletteOffset] = useState(() => Math.floor(Math.random() * 360))
  const [slide, setSlide] = useState<{ direction: -1 | 1; outgoingHue: number } | null>(null)
  const [catalogStatus, setCatalogStatus] = useState('Loading voices…')
  const [playingId, setPlayingId] = useState('')
  const [loadingId, setLoadingId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const request = new AbortController()
    const audio = audioRef.current

    async function loadVoices() {
      try {
        const response = await fetch('/api/voice/catalog', { signal: request.signal })
        if (!response.ok) throw new Error()
        const entries: Persona[] = await response.json()
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
    return () => {
      request.abort()
      previewRequest.current?.abort()
      audio?.pause()
    }
  }, [])

  async function togglePreview(voice: Persona) {
    const audio = audioRef.current
    if (!audio) return
    if (playingId === voice.id) {
      audio.pause()
      return
    }

    previewRequest.current?.abort()
    const request = new AbortController()
    previewRequest.current = request
    audio.pause()
    setPlayingId('')
    setLoadingId(voice.id)
    setError('')

    try {
      const response = await fetch(`/api/voice/catalog/${voice.id}/preview`, { signal: request.signal })
      const preview = await response.json().catch(() => null)
      if (!response.ok) {
        if (!request.signal.aborted) {
          setError(typeof preview?.detail === 'string' ? preview.detail : 'The voice preview server is unavailable.')
        }
        return
      }
      const url = preview?.url
      if (typeof url !== 'string') throw new Error()
      if (request.signal.aborted) return
      audio.src = url
      await audio.play()
      if (!request.signal.aborted) setPlayingId(voice.id)
    } catch {
      if (!request.signal.aborted) setError(`Could not play the sample for ${voice.name}.`)
    } finally {
      if (!request.signal.aborted) setLoadingId('')
    }
  }

  const search = query.trim().toLowerCase()
  const visibleVoices = voices.filter(voice => `${voice.name} ${voice.tagline}`.toLowerCase().includes(search))

  const voice = visibleVoices[activeIndex]
  const selected = voice ? selectedIds.includes(voice.id) : false
  // A large hue step keeps neighbors visually distinct, even in filtered results.
  const hue = (paletteOffset + activeIndex * 137) % 360
  const colorStyle = {
    '--voice-hue': hue,
    '--voice-slide': `${(slide?.direction ?? 1) * 76}px`,
  } as CSSProperties

  function showVoice(index: number, animate = true) {
    if (animate && index === activeIndex) return
    setSlide(animate ? { direction: index > activeIndex ? 1 : -1, outgoingHue: hue } : null)
    previewRequest.current?.abort()
    audioRef.current?.pause()
    setPlayingId('')
    setLoadingId('')
    setError('')
    setActiveIndex(index)
  }

  return (
    <section className="voice-auditions" aria-labelledby="voice-auditions-title">
      <h3 id="voice-auditions-title">Pick your favorite voices</h3>
      <label className="mt-3 block">
        Search voices
        <input
          className="form-field"
          type="search"
          value={query}
          onChange={event => {
            setQuery(event.target.value)
            showVoice(0, false)
          }}
        />
      </label>
      <p role="status" className="muted mt-2">
        {catalogStatus || (voice
          ? `Voice ${activeIndex + 1} of ${visibleVoices.length} · ${selectedIds.length} saved`
          : 'No voices match your search.')}
      </p>
      {voice && (
        <div className="voice-carousel" aria-label="Voice preview" style={colorStyle}>
          <div className="voice-carousel-info" aria-live="polite">
            <h4>{voice.name}</h4>
            {voice.tagline && <p className="muted">{voice.tagline}</p>}
          </div>
          <div className="voice-carousel-stage">
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
                <Orb />
              </div>
            </div>
            <button
              type="button"
              className="voice-carousel-arrow"
              aria-label="Next voice"
              disabled={activeIndex === visibleVoices.length - 1}
              onClick={() => showVoice(activeIndex + 1)}
            >
              <span aria-hidden="true">›</span>
            </button>
          </div>
          <div className="voice-carousel-dots" role="group" aria-label="Nearby voices">
            {visibleVoices.map((dotVoice, index) => {
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
          <div className="voice-carousel-actions">
            <button
              type="button"
              className="voice-audition-button"
              disabled={!voice.can_preview || loadingId === voice.id}
              aria-label={`${playingId === voice.id ? 'Pause' : 'Preview'} ${voice.name}`}
              onClick={() => void togglePreview(voice)}
            >
              {!voice.can_preview ? 'No sample' : loadingId === voice.id ? 'Loading…' : playingId === voice.id ? 'Pause' : 'Play'}
            </button>
            <button type="button" className="voice-audition-button" aria-pressed={selected} onClick={() => onToggle(voice.id)}>
              {selected ? '✓ Saved' : 'Save voice'}
            </button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
      <audio ref={audioRef} preload="none" onPause={() => setPlayingId('')} onEnded={() => setPlayingId('')} />
    </section>
  )
}
