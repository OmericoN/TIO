import { CommitStrategy, useScribe } from '@elevenlabs/react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { AgentStage } from './AgentStage'
import { Sources } from './BriefingHistory'
import { audioUrl, historyStore, recordedTurns } from './briefings'
import type { Briefing, DiscussionPlan, Entry, Headline, Preferences, Segment } from './briefings'

async function post<T>(url: string, body = {}): Promise<T> {
  const response = await fetch('/api/panel' + url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.[0]?.msg || 'Panel unavailable. Check the backend connection.')
  }
  return response.json()
}

async function streamTurn(id: string, body: object, status: (value: string) => void): Promise<Segment | { done: true }> {
  const response = await fetch(`/api/panel/${id}/events`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok || !response.body) throw new Error('Panel session unavailable.')
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) throw new Error('Panel stream ended before audio arrived.')
      const lines = (buffer + value).split('\n')
      buffer = lines.pop()!
      for (const line of lines) {
        const event = JSON.parse(line)
        if (event.error) throw new Error(event.error)
        if (event.result) return event.result
        if (event.status) status(event.status)
      }
    }
  } finally { reader.releaseLock() }
}

// A soft two-note cue between stories, synthesized so no audio file is needed. Resolves when it has finished.
function storyCue(context: AudioContext | null): Promise<void> {
  if (!context || context.state === 'closed') return Promise.resolve()
  void context.resume().catch(() => {})
  const start = context.currentTime + 0.05
  for (const [i, frequency] of [659.25, 987.77].entries()) {
    const tone = context.createOscillator(), gain = context.createGain()
    tone.type = 'sine'
    tone.frequency.value = frequency
    const at = start + i * 0.16
    gain.gain.setValueAtTime(0, at)
    gain.gain.linearRampToValueAtTime(0.12, at + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.001, at + 0.6)
    tone.connect(gain).connect(context.destination)
    tone.start(at)
    tone.stop(at + 0.65)
  }
  return new Promise(resolve => setTimeout(resolve, 850))
}

type Props = {
  owner: string
  stories: Headline[]
  preferences: Preferences
  plan?: DiscussionPlan
  recorded: boolean
  origin: { x: number; y: number; context: AudioContext | null }
  onClose: () => void
}

export function PanelPlayer({ owner, stories, preferences, plan, recorded, origin, onClose }: Props) {
  const [running, setRunning] = useState(true)
  const [status, setStatus] = useState('Opening your briefing…')
  const [active, setActive] = useState<Segment | null>(null)
  const [sample, setSample] = useState(recorded)
  const [speaker, setSpeaker] = useState('')
  const [host, setHost] = useState('Main Host')
  const [guests, setGuests] = useState<string[]>([])
  const [saveError, setSaveError] = useState(false)
  const [record] = useState<Briefing>(() => ({ id: crypto.randomUUID(), date: new Date().toISOString(), titles: stories.map(s => s.title), entries: [], sample: recorded }))
  const sampleQueue = useRef<Segment[]>([])
  const previousStory = useRef('')
  const cueContext = useRef<AudioContext | null>(origin.context)
  const dialog = useRef<HTMLDialogElement>(null)
  const consolePanel = useRef<HTMLDivElement>(null)
  const [question, setQuestion] = useState('')
  const player = useRef<HTMLAudioElement>(null)
  const session = useRef('')
  const generation = useRef(0)
  const sessionStart = useRef(0)
  const pendingInterrupt = useRef<Promise<unknown> | null>(null)
  const current = useRef<Segment | null>(null)
  const finished = useRef<{ heard: string; seconds: number; fully_heard: boolean } | null>(null)
  // The host's pre-voiced holding lines, loaded when the briefing starts and played while a question is answered.
  const holding = useRef<{ speaker: string; clips: { text: string; audio: string }[]; last: number }>({ speaker: '', clips: [], last: -1 })
  const holdingPlayback = useRef<{ audio: HTMLAudioElement; done: () => void } | null>(null)

  async function loadHolding(id: string) {
    try {
      const response = await fetch(`/api/panel/${id}/holding`)
      if (!response.ok || session.current !== id) return
      const { speaker, clips } = await response.json()
      if (Array.isArray(clips)) holding.current = { speaker, clips, last: -1 }
    } catch { /* Questions still work without holding lines. */ }
  }

  function stopHolding() {
    const playing = holdingPlayback.current
    holdingPlayback.current = null
    if (playing) { playing.audio.pause(); playing.done() }
  }

  // Plays a different holding line each time; resolves when it ends, fails or is stopped.
  function playHolding(): Promise<void> {
    stopHolding()
    const { clips, speaker, last } = holding.current
    if (!clips.length) return Promise.resolve()
    const choices = clips.map((_, i) => i).filter(i => i !== last || clips.length === 1)
    const index = choices[Math.floor(Math.random() * choices.length)]
    holding.current.last = index
    const audio = new Audio('data:audio/mpeg;base64,' + clips[index].audio)
    return new Promise(resolve => {
      const done = () => { if (holdingPlayback.current?.audio === audio) holdingPlayback.current = null; setSpeaker(''); resolve() }
      holdingPlayback.current = { audio, done }
      audio.onended = done
      audio.onerror = done
      setSpeaker(speaker)
      void audio.play().catch(done)
    })
  }

  useEffect(() => { consolePanel.current?.scrollTo(0, 0) }, [active?.id])

  function save(entry: Entry) {
    if (record.entries.some(e => e.id === entry.id)) return
    record.entries.push(entry)
    void historyStore(owner, { ...record, entries: [...record.entries] }).catch(() => setSaveError(true))
  }

  function introduce() {
    const line = current.current, audio = player.current
    if (!line || !audio || audio.paused) return
    const spoken = Number.isFinite(audio.duration) ? audio.currentTime / audio.duration : 0
    const appearing = line.guests.filter(name => {
      if (line.speaker === name) return true
      const at = line.text.toLowerCase().indexOf(name.toLowerCase())
      return at >= 0 && spoken >= at / line.text.length
    })
    if (appearing.length) setGuests(names => appearing.every(n => names.includes(n)) ? names : [...new Set([...names, ...appearing])])
  }

  function show(segment: Segment | null) {
    current.current = segment
    if (segment) finished.current = null
    setActive(segment)
    const audio = player.current
    if (!audio) return
    audio.pause()
    if (!segment) {
      audio.removeAttribute('src')
      audio.load()
      return
    }
    setHost(segment.host)
    let cue: Promise<void> = Promise.resolve()
    if (previousStory.current !== segment.story) {
      setGuests([])
      if (previousStory.current) {
        try { cueContext.current ??= new AudioContext() } catch { /* Play without the cue. */ }
        cue = storyCue(cueContext.current)
      }
      previousStory.current = segment.story
    }
    audio.src = audioUrl(segment)
    const opening = dialog.current?.getAnimations().filter(a => a.playState === 'running') ?? []
    void Promise.allSettled([cue, ...opening.map(a => a.finished)]).then(() => {
      if (current.current === segment && audio.isConnected) {
        void audio.play().catch(() => { if (current.current === segment) setStatus('Press play to listen.') })
      }
    })
  }

  function end() {
    stopHolding()
    generation.current++
    if (session.current) void fetch('/api/panel/' + session.current, { method: 'DELETE' }).catch(() => {})
    session.current = ''
    pendingInterrupt.current = null
    scribe.disconnect()
    show(null)
    setSpeaker('')
    setRunning(false)
  }

  function fail(error: unknown) {
    end()
    setStatus(error instanceof Error ? error.message : 'Panel unavailable.')
  }

  async function next(body: object = {}, before?: Promise<void>) {
    const id = session.current, gen = generation.current
    if (!id) return
    setStatus('question' in body ? 'Finding the answer in your sources…' : 'Preparing a verified segment…')
    try {
      const result = await streamTurn(id, { ...body, generation: gen - sessionStart.current }, message => {
        if (session.current === id && generation.current === gen) setStatus(message)
      })
      if (session.current !== id || generation.current !== gen) return
      if (before) {
        await before  // let the holding line finish rather than cutting it off mid-sentence
        if (session.current !== id || generation.current !== gen) return
      }
      if ('done' in result) { end(); setStatus('Briefing complete.'); return }
      setStatus('')
      show(result)
    } catch (e) { if (id === session.current && gen === generation.current) fail(e) }
  }

  function interrupt() {
    if (!session.current || pendingInterrupt.current) return
    stopHolding()
    const heard = current.current?.id, seconds = player.current?.currentTime || 0
    const gen = ++generation.current
    show(null)
    setStatus('Listening for your question…')
    const position = finished.current ? { ...finished.current, completed: true } : { heard, seconds }
    pendingInterrupt.current = post(`/${session.current}/interrupt`, { generation: gen - sessionStart.current, ...position })
    void pendingInterrupt.current.catch(e => { if (generation.current === gen) fail(e) })
  }

  async function ask(text: string) {
    if (!text.trim() || !session.current) return
    save({ id: crypto.randomUUID(), speaker: 'Listener', text: text.trim() })
    interrupt()
    const gen = generation.current
    const thinking = playHolding()  // the host starts "thinking aloud" at once, while the answer is prepared
    try {
      await pendingInterrupt.current
      if (generation.current !== gen) return
      pendingInterrupt.current = null
      await next({ question: text }, thinking)
    } catch (e) { if (generation.current === gen) fail(e) }
  }

  const scribe = useScribe({
    modelId: 'scribe_v3_live', commitStrategy: CommitStrategy.VAD,
    microphone: { echoCancellation: true, noiseSuppression: true },
    // Two words before pausing, so a cough or the speakers' own echo doesn't stop the briefing.
    onPartialTranscript: ({ text }) => { if (text.trim().split(/\s+/).length >= 2) interrupt() },
    onCommittedTranscript: ({ text }) => { void ask(text) },
    onError: () => setStatus('Microphone unavailable. You can type your question.'),
  })

  useLayoutEffect(() => {
    const modal = dialog.current!
    modal.showModal()
    const before = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { modal.close(); document.body.style.overflow = before }
  }, [])

  useEffect(() => {
    const audio = player.current!
    void start()
    return () => {
      generation.current++
      audio.pause()
      scribe.disconnect()
      if (session.current) void fetch('/api/panel/' + session.current, { method: 'DELETE' }).catch(() => {})
      session.current = ''
    }
  }, [])

  function advanceSample() {
    const line = sampleQueue.current.shift()
    if (line) { setStatus(''); show(line) }
    else { end(); setStatus('Briefing complete.') }
  }

  async function start() {
    const gen = ++generation.current
    if (recorded) {
      sampleQueue.current = recordedTurns(stories)
      advanceSample()
      return
    }
    setStatus('Preparing your briefing…')
    try {
      const lineup = plan ? { briefing_plan: plan } : { story_ids: stories.map(s => s.id) }
      const result = await post<{ id: string; sample: boolean }>('', { ...preferences, ...lineup })
      if (generation.current !== gen) {
        await fetch('/api/panel/' + result.id, { method: 'DELETE' })
        return
      }
      session.current = result.id
      sessionStart.current = gen
      void loadHolding(result.id)
      record.sample = result.sample
      setSample(result.sample)
      await next()
    } catch (e) { if (generation.current === gen) fail(e) }
  }

  async function microphone() {
    const id = session.current
    try {
      const { token } = await post<{ token: string }>(`/${id}/microphone`)
      if (session.current === id) await scribe.connect({ token })
      if (session.current !== id) scribe.disconnect()
    } catch { setStatus('Could not enable the microphone. You can type your question.') }
  }

  return <dialog ref={dialog} className="session-glass" aria-labelledby="room-title"
    style={{ '--launch-x': origin.x + 'px', '--launch-y': origin.y + 'px' } as CSSProperties}
    onCancel={e => { e.preventDefault(); onClose() }}>
    <div className="session-inner">
      <div className="session-intro"><header className="session-heading"><div><h1 id="room-title">Your briefing</h1>
        <p>{recorded ? 'Recorded sample' : 'Synthetic narration'}{sample ? ' · Fictional reporting' : ''}</p></div>
        <button autoFocus onClick={onClose} className="room-close">{running ? 'End session' : 'Close'}</button>
      </header><h2 className="session-headline">{active?.story ?? stories[0].title}</h2></div>
      <AgentStage audio={player} origin={origin} host={host} guests={guests} speaker={speaker} />
      <div ref={consolePanel} className="session-console">
      <p role="status" className="player-status">{status}</p>
      {saveError && <p role="alert">This browser could not save your briefing. Check the available device storage.</p>}
      <audio ref={player} controls aria-label="Briefing audio" hidden={!active} className="w-full"
        onPlaying={() => { const line = current.current; if (line) { setSpeaker(line.speaker); setStatus(''); save(line); introduce() } }}
        onPause={() => setSpeaker('')} onTimeUpdate={introduce}
        onError={() => { if (current.current) fail(new Error('This audio could not play. Close the session and try again.')) }} onEnded={() => {
      const heard = current.current
      if (recorded) { advanceSample(); return }
      if (!session.current || !heard) return
      const audio = player.current!
      let seconds = 0
      for (let i = 0; i < audio.played.length; i++) seconds += audio.played.end(i) - audio.played.start(i)
      finished.current = { heard: heard.id, seconds, fully_heard: seconds >= audio.duration - 0.15 }
      show(null)
      void next(finished.current)
    }} />
    {active && <div className="playing-segment">
      <article><p className="topic">{active.speaker}</p><p>{active.text}</p></article>
      <Sources cards={active.sources} />
    </div>}
    {running && !recorded && <form className="flex flex-wrap gap-2" onSubmit={e => {
      e.preventDefault(); void ask(question); setQuestion('')
    }}>
      <button className="button" type="button" disabled={!session.current || scribe.status === 'connecting'}
        onClick={scribe.isConnected ? scribe.disconnect : microphone}>{scribe.isConnected ? 'Disable microphone' : 'Enable microphone'}</button>
      <input className="grow rounded border p-2" aria-label="Question for the panel" placeholder="Ask the panel"
        value={question} maxLength={1000} onChange={e => setQuestion(e.target.value)} />
      <button className="button" disabled={!question.trim() || !session.current}>Ask</button>
      {pendingInterrupt.current && <button type="button" onClick={async () => {
        const gen = generation.current
        try {
          await pendingInterrupt.current
          if (generation.current === gen && pendingInterrupt.current) {
            pendingInterrupt.current = null
            await next()
          }
        } catch (e) { if (generation.current === gen) fail(e) }
      }}>Resume briefing</button>}
    </form>}
      </div>
    </div>
  </dialog>
}
