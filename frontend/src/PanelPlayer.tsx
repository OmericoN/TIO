import { CommitStrategy, Conversation, useScribe } from '@elevenlabs/react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { AgentStage } from './AgentStage'
import { Icon } from './Brand'
import { BriefingSetup } from './BriefingSetup'
import { Sources } from './BriefingHistory'
import { audioUrl, factChecksAt, historyStore, recordedTurns } from './briefings'
import { RealtimeAgent } from './realtime'
import { VisualHistory, VisualPanel } from './Visual'
import type { Visual } from './Visual'
import type { AgentInfo, Spoken } from './realtime'
import type { Briefing, Card, DiscussionPlan, Entry, Headline, Preferences, Segment } from './briefings'

async function post<T>(url: string, body = {}): Promise<T> {
  const response = await fetch('/api/panel' + url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Panel unavailable. Check the backend connection.')
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
  recorded: boolean
  canGenerateSample: boolean
  origin: { x: number; y: number; context: AudioContext | null }
  // A signed-in listener: the briefing is planned from their recommended feed and picks once they confirm.
  listener?: { id: string; articleIds: string[]; selectedIds: string[] }
  onClose: () => void
}

// How likely each other panelist is to react over a line (short asides, never facts).
const REACTION_CHANCE = 0.45

async function postJson<T>(url: string, body: object): Promise<T> {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const result = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof result?.detail === 'string' ? result.detail : 'The briefing could not be planned.')
  return result
}

export function PanelPlayer({ owner, stories: chosenStories, preferences, recorded: recordedDefault, canGenerateSample, origin, listener, onClose }: Props) {
  // The agenda: the chosen headlines, replaced by the planned lineup for a signed-in listener.
  const [stories, setStories] = useState<Headline[]>(chosenStories.length ? chosenStories
    : [{ id: 'planning', title: 'Your briefing', topic: '', summary: '' }])
  const [recorded, setRecorded] = useState(recordedDefault)
  const [running, setRunning] = useState(true)
  const [status, setStatus] = useState('')
  const [phase, setPhase] = useState<'setup' | 'confirming' | 'playing'>('setup')
  const [minutes, setMinutes] = useState(preferences.minutes)
  const [mode, setMode] = useState(preferences.mode)
  const [experts, setExperts] = useState(2)
  const confirmed = useRef(false)
  const [active, setActive] = useState<Segment | null>(null)
  const [begun, setBegun] = useState(false)
  const [topic, setTopic] = useState(0)
  const [complete, setComplete] = useState(false)
  const [facts, setFacts] = useState<Card[]>([])
  const [visual, setVisual] = useState<Visual | null>(null)
  const [visualLeaving, setVisualLeaving] = useState(false)
  const [visualPinned, setVisualPinned] = useState(false)
  const [visualHistory, setVisualHistory] = useState<Visual[]>([])
  const [reactions, setReactions] = useState<{ id: number; who: string; text: string }[]>([])

  // A panelist's aside, shown briefly next to the conversation while it's heard.
  function showReaction(who: string, text: string) {
    const id = Math.random()
    setReactions(shown => [...shown.slice(-2), { id, who, text }])
    window.setTimeout(() => setReactions(shown => shown.filter(r => r.id !== id)), 2600)
  }
  const visualTimers = useRef<number[]>([])
  const pinnedRef = useRef(false)

  // A visual stays up while its line plays, then fades away a few seconds after, unless the listener keeps it.
  function showVisual(next: Visual) {
    visualTimers.current.forEach(clearTimeout)
    visualTimers.current = []
    setVisualLeaving(false)
    pinnedRef.current = false
    setVisualPinned(false)
    setVisual(next)
    setVisualHistory(shown => shown.some(v => v.kind === next.kind && v.title === next.title) ? shown : [...shown, next])
  }

  function pinVisual(pinned: boolean) {
    pinnedRef.current = pinned
    setVisualPinned(pinned)
    if (!pinned) fadeVisual(3000)
  }

  function fadeVisual(afterMs: number) {
    if (pinnedRef.current) return  // kept on screen until the listener lets it go
    visualTimers.current.forEach(clearTimeout)
    visualTimers.current = [
      window.setTimeout(() => setVisualLeaving(true), afterMs),
      window.setTimeout(() => { setVisual(null); setVisualLeaving(false) }, afterMs + 700),
    ]
  }
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
  // A live speech-to-speech conversation with the host while the briefing is paused for the listener.
  const live = useRef<{ gen: number; transcript: { role: 'listener' | 'host'; text: string }[]; conversation?: Conversation;
    idle?: number; finishing?: boolean; micWasOn: boolean } | null>(null)
  const [liveWith, setLiveWith] = useState('')
  // Experimental live host: the whole briefing runs over one speech-to-speech session, fed fact cards by the backend.
  const liveShow = false  // this branch runs every briefing as the realtime panel
  // Experimental realtime panel: a speech-to-speech agent per speaker, each speaking from briefs the backend plans.
  // On this experiment branch every generated briefing is live: panel and presenter formats both run as realtime
  // agents (presenter = the host alone). Only the 'live' format keeps its single ElevenLabs session, and the
  // recorded sample still plays its fixed audio.
  const realtimeMode = !recorded  // whatever the saved format: a host plus guests, each a separate live voice
  const rt = useRef<{ agents: Map<string, RealtimeAgent>; host?: RealtimeAgent; microphone: MediaStream | null;
    current?: RealtimeAgent; listening: boolean; exchange: { role: 'listener' | 'host'; text: string }[];
    quiet?: number; duckTimer?: number; release?: () => void; talking?: boolean; generation: number;
    skipping?: boolean; cutting?: RealtimeAgent; cutTimeout?: number } | null>(null)

  function resumePanel(state: NonNullable<typeof rt.current>) {
    clearTimeout(state.quiet)
    state.listening = false
    state.release?.()
    state.release = undefined
    setStatus('')
  }

  // Hold to talk: whoever is speaking stops, the microphone opens, and on release the host hears the question.
  function holdToTalk(down: boolean) {
    const state = rt.current
    if (!state?.host || state.talking === down) return
    state.talking = down
    if (down) {
      state.listening = true
      clearTimeout(state.quiet)
      state.current?.cancel()
      state.cutting?.cancel()
      state.host.startListening()
      setStatus('Listening… release to ask')
    } else {
      state.host.stopListening()
      setStatus('Thinking about your question…')
      clearTimeout(state.duckTimer)
      state.duckTimer = window.setTimeout(() => resumePanel(state), 8000)  // nothing transcribed: carry on
    }
  }

  // The listener asked something (spoken or typed): stop whoever is talking and have the host answer live.
  function listenerAsks(text: string) {
    const state = rt.current
    if (!state?.host) return
    state.listening = true
    clearTimeout(state.quiet)
    state.current?.duck(1)
    state.current?.cancel()
    state.cutting?.cancel()
    state.exchange.push({ role: 'listener', text })
    save({ id: crypto.randomUUID(), speaker: 'Listener', text })
    setStatus('Answering your question…')
    const host = state.host
    // If they want another story, the backend switches now and plans its opening while the host bridges with a
    // short teaser, so the panel is ready the moment the host stops talking.
    void post<{ intent: string; headline?: string; note?: string; visual?: Visual | null }>(`/${session.current}/realtime/listen`,
      { generation: state.generation, text })
      .catch(() => ({ intent: 'question' as string, note: undefined, headline: undefined, visual: undefined }))
      .then(result => {
        if (rt.current !== state) return
        state.skipping = result.intent === 'skip'
        if (state.skipping) setStatus(`Moving on to: ${result.headline}`)
        if (result.visual) showVisual(result.visual)  // what they asked to see, built from the story's reporting
        host.answer(text, result.note)
      })
    state.quiet = window.setTimeout(() => { state.listening = false; state.release?.(); state.release = undefined }, 15000)
  }
  const showSession = useRef<{ conversation?: Conversation; cue?: number; heardListener: number; ending: boolean;
    startedAt: number; story: string } | null>(null)

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
  useEffect(() => {
    dialog.current?.querySelector('[aria-current="step"]')?.scrollIntoView({
      block: 'nearest', inline: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
    })
  }, [topic, begun])

  function save(entry: Entry) {
    if (record.entries.some(e => e.id === entry.id)) return
    record.entries.push(entry)
    void historyStore(owner, { ...record, entries: [...record.entries] }).catch(() => setSaveError(true))
  }

  function introduce() {
    const line = current.current, audio = player.current
    if (!line || !audio) return
    const spoken = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.currentTime / audio.duration : 0
    const cues = factChecksAt(line, spoken)
    setFacts(previous => previous.length === cues.length && previous.every(c => cues.includes(c)) ? previous : cues)
    if (audio.paused) return
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
    setFacts(segment ? factChecksAt(segment, 0) : [])
    if (segment) {
      const index = stories.findIndex(story => segment.story_id ? story.id === segment.story_id : story.title === segment.story)
      if (index >= 0) setTopic(index)
    }
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
    const panel = rt.current
    if (panel) {
      rt.current = null
      clearTimeout(panel.quiet)
      panel.release?.()
      panel.agents.forEach(agent => agent.close())
      panel.microphone?.getTracks().forEach(track => track.stop())
    }
    const showing = showSession.current
    if (showing) { showSession.current = null; clearTimeout(showing.cue); void showing.conversation?.endSession().catch(() => {}) }
    if (live.current) { live.current.finishing = true; void live.current.conversation?.endSession().catch(() => {}); live.current = null; setLiveWith('') }
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
      if ('done' in result) { setComplete(true); end(); setStatus('Briefing complete.'); return }
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
    if (live.current?.conversation) {  // already talking live: the typed question joins that conversation
      live.current.transcript.push({ role: 'listener', text: text.trim() })
      live.current.conversation.sendUserMessage(text.trim())
      return
    }
    interrupt()
    const gen = generation.current
    const thinking = playHolding()  // the host starts "thinking aloud" at once, while the answer is prepared
    try {
      await pendingInterrupt.current
      if (generation.current !== gen) return
      pendingInterrupt.current = null
      if (await startLive(text.trim(), gen)) return  // the host answers live; falls back to a verified segment
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
    return () => {
      generation.current++
      audio.pause()
      scribe.disconnect()
      if (session.current) void fetch('/api/panel/' + session.current, { method: 'DELETE' }).catch(() => {})
      session.current = ''
    }
  }, [])

  async function confirm() {
    if (confirmed.current) return
    confirmed.current = true
    setPhase('confirming')
    const gen = generation.current
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const dials = dialog.current?.querySelector('.setup-dials')
      const orb = dialog.current?.querySelector('.host-confirm')
      const digest = dials?.animate([{ transform: 'scale(1)', opacity: 1 }, { transform: 'scale(.36)', opacity: 0 }],
        { duration: 280, easing: 'cubic-bezier(.77,0,.175,1)', fill: 'forwards' })
      const drop = orb?.animate([{ transform: 'scale(1.07,.89)' }, { transform: 'scale(.94,1.09)', offset: .4 }, { transform: 'scale(1)' }],
        { duration: 480, easing: 'cubic-bezier(.34,1.56,.64,1)' })
      await Promise.allSettled([digest?.finished, drop?.finished])
    }
    if (generation.current !== gen) return
    setPhase('playing')
    void start()
  }

  function advanceSample() {
    const line = sampleQueue.current.shift()
    if (line) { setStatus(''); show(line) }
    else { setComplete(true); end(); setStatus('Briefing complete.') }
  }

  async function start() {
    const gen = ++generation.current
    if (recorded) {
      record.sample = true
      setSample(true)
      sampleQueue.current = recordedTurns(stories)
      advanceSample()
      return
    }
    setStatus('Preparing your selected headlines…')
    try {
      // A signed-in listener's briefing is planned now, from their feed, picks, and the minutes and experts just
      // chosen; otherwise the panel discusses the selected headlines.
      let lineup: object = { expert_count: experts, story_ids: stories.map(s => s.id) }
      if (listener) {
        setStatus('Planning your briefing…')
        const plan = await planBriefing(listener)
        if (generation.current !== gen) return
        lineup = { briefing_plan: plan }
      }
      const result = await post<{ id: string; sample: boolean }>('', { ...preferences, mode: 'panel', minutes, ...lineup })
      if (generation.current !== gen) {
        await fetch('/api/panel/' + result.id, { method: 'DELETE' })
        return
      }
      session.current = result.id
      sessionStart.current = gen
      void loadHolding(result.id)
      record.sample = result.sample
      setSample(result.sample)
      if (liveShow) await startShow(gen)
      else if (realtimeMode) await startRealtime(gen)
      else await next()
    } catch (e) {
      if (generation.current === gen) {
        fail(e)
        confirmed.current = false
        setRunning(true)
        setPhase('setup')
        dialog.current?.querySelector('.setup-dials')?.getAnimations().forEach(animation => animation.cancel())
      }
    }
  }

  // Realtime panel: connect every agent, then loop: fetch the next brief (usually planned while the previous agent
  // spoke), have its agent speak it, and report what was actually said. The host hears the microphone; when the
  // listener talks, whoever is speaking stops, the host answers live, and after a quiet moment the panel resumes.
  async function startRealtime(gen: number) {
    const id = session.current
    setStatus('Connecting the panel…')
    const { model, agents } = await post<{ model: string; agents: AgentInfo[] }>(`/${id}/realtime/agents`)
    let microphone: MediaStream | null = null
    try { microphone = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }) }
    catch { setStatus('Microphone unavailable. You can type questions.') }
    if (session.current !== id || generation.current !== gen) { microphone?.getTracks().forEach(t => t.stop()); return }
    const state: NonNullable<typeof rt.current> = { agents: new Map(), microphone, listening: false, exchange: [],
      generation: gen - sessionStart.current }
    rt.current = state
    const story = { title: stories[0]?.title ?? '' }
    const quietThenResume = (ms: number) => {
      clearTimeout(state.quiet)
      state.quiet = window.setTimeout(() => { state.listening = false; state.release?.(); state.release = undefined }, ms)
    }
    const hostInfo = agents.find(agent => agent.role === 'host')!
    const connected = await Promise.all(agents.map(info => RealtimeAgent.connect(info, model,
      info.role === 'host' ? microphone : null, info.role !== 'host' ? {} : {
        // Push-to-talk: the microphone is only open while the talk button is held, so this is always the listener.
        onListenerText: text => {
          if (rt.current !== state) return
          clearTimeout(state.duckTimer)
          if (!text.split(/\s+/).some(word => /\w/.test(word))) { resumePanel(state); return }
          listenerAsks(text)
        },
        onReply: text => {
          if (rt.current !== state) return
          state.exchange.push({ role: 'host', text })
          const line: Segment = { id: crypto.randomUUID(), story: story.title, speaker: `${hostInfo.name} · ${hostInfo.voice} · answering you`, host: hostInfo.name,
            guests: [], text, sources: [] }
          setActive(line)
          save(line)
        },
        // A pause, then back to the panel; straight on after a skip, since the next story is already planned.
        onReplyDone: () => {
          if (rt.current !== state) return
          quietThenResume(state.skipping ? 600 : 3500)
          state.skipping = false
          fadeVisual(4000)  // a visual the listener asked for fades once the host has talked them through it
        },
      })))
    if (rt.current !== state) { connected.forEach(agent => agent.close()); return }
    connected.forEach(agent => state.agents.set(agent.info.id, agent))
    state.host = state.agents.get(hostInfo.id)
    setHost(hostInfo.name)
    console.info('Realtime panel voices', Object.fromEntries(agents.map(a => [a.name, a.voice])))
    const liveStatus = `Live · ${agents.length} realtime ${agents.length === 1 ? 'voice' : 'voices'}${microphone ? ' · hold the talk button to join in' : ''}`
    setStatus(liveStatus)
    const waitForListener = () => state.listening
      ? new Promise<void>(resolve => { state.release = resolve })
      : Promise.resolve()

    let step: object = { generation: gen - sessionStart.current }
    while (rt.current === state) {
      type Turn = { done?: boolean; context?: string | null; id: string; speaker_id: string; speaker: string;
        guests: string[]; story: string; story_id: string; brief: string; max_seconds: number; sources: Segment['sources']
        visual: Visual | null; cut_off: boolean }
      const turn = await post<Turn>(`/${id}/realtime/next`, step)
      if (rt.current !== state) return
      if (turn.context) state.host?.context(turn.context)
      // Every agent hears the panel: each spoken line goes to all of them, so guests can react to each other.
      if (turn.done) { setComplete(true); end(); setStatus('Briefing complete.'); return }
      await waitForListener()
      setStatus(liveStatus)
      const agent = state.agents.get(turn.speaker_id)
      if (!agent) throw new Error(`No agent connected for ${turn.speaker}.`)
      story.title = turn.story
      if (previousStory.current !== turn.story) {
        setGuests([])
        pinVisual(false); fadeVisual(0)  // a new story starts without the previous story's visual
        if (previousStory.current) await storyCue(cueContext.current ??= new AudioContext())
        previousStory.current = turn.story
      }
      setGuests(turn.guests)
      setSpeaker(turn.speaker)
      setBegun(true)
      setFacts(turn.sources)
      if (turn.visual) showVisual(turn.visual)
      setStories(lineup => {
        const index = lineup.findIndex(story => story.id === turn.story_id || story.title === turn.story)
        if (index >= 0) setTopic(index)
        return lineup
      })
      const voice = agents.find(a => a.id === turn.speaker_id)?.voice
      const line: Segment = { id: turn.id, story: turn.story, speaker: voice ? `${turn.speaker} · ${voice}` : turn.speaker, host: hostInfo.name,
        guests: turn.guests, text: '…', sources: turn.sources }
      setActive(line)
      state.current = agent
      // Cut-ins: when the producer planned that the next speaker jumps in on this one, the loop moves on partway
      // through this line; the next voice starts over it, and this one is cut off a beat later.
      let soFar = '', startedAt = performance.now(), cutTimer = 0
      const asideTimers: number[] = []
      let cut = () => {}
      const cutting = new Promise<'cut'>(resolve => { cut = () => resolve('cut') })
      // The transcript appears as the agent improvises it, word by word, rather than as a finished script.
      const speaking = agent.speak(turn.brief, turn.max_seconds, text => { soFar = text; setActive({ ...line, text }) }, () => {
        startedAt = performance.now()
        // Crosstalk: the others now and then react over this line ("Mm-hmm", "Oh, come on"), in character.
        const listeners = [...state.agents.values()].filter(other => other !== agent && other !== state.cutting)
        for (const other of listeners) {
          if (Math.random() > REACTION_CHANCE) continue
          const at = turn.max_seconds * (0.25 + Math.random() * 0.55) * 1000
          asideTimers.push(window.setTimeout(() => {
            if (rt.current !== state || state.listening || state.current !== agent) return
            other.react(`${turn.speaker} is saying: "${soFar.slice(-220)}"`, text => showReaction(other.info.name, text))
          }, at))
        }
        const victim = state.cutting
        if (victim) {  // this line is cutting in on the last one: both talk at once for a second, then they give up
          state.cutting = undefined
          clearTimeout(state.cutTimeout)
          window.setTimeout(() => {
            victim.cancel()
            // Half the time the one cut off protests on the way out.
            if (Math.random() < 0.5) window.setTimeout(() => victim.react(
              `${turn.speaker} just talked over you before you could finish. Protest in two or three words ("Hey, let me—").`,
              text => showReaction(victim.info.name, text)), 150)
          }, 1800)
        }
        // Cut early: a third to a half of the way into the line, before they've landed their point.
        if (turn.cut_off) cutTimer = window.setTimeout(cut, Math.max(3, turn.max_seconds * (0.3 + Math.random() * 0.2)) * 1000)
      })
      const outcome = await Promise.race([speaking, cutting])
      clearTimeout(cutTimer)
      asideTimers.forEach(clearTimeout)
      const cutOff = outcome === 'cut' && !state.listening
      const said: Spoken = outcome === 'cut' ? { text: soFar, seconds: (performance.now() - startedAt) / 1000, interrupted: false } : outcome
      if (cutOff) {
        state.cutting = agent  // still talking until the next voice is audible (or three seconds pass)
        state.cutTimeout = window.setTimeout(() => { agent.cancel(); if (state.cutting === agent) state.cutting = undefined }, 3000)
      } else if (outcome === 'cut') agent.cancel()
      fadeVisual(3000)  // the line that used it is done
      state.current = undefined
      setSpeaker('')
      if (rt.current !== state) return
      if (said.text) {
        for (const other of state.agents.values()) {
          if (other !== agent) other.context(`ON AIR (not addressed to you unless named): ${turn.speaker} said: "${said.text}${cutOff ? '—" (cut off mid-sentence)' : '"'}`)
        }
        const spoken = { ...line, text: said.text }
        setActive(spoken)
        save(spoken)
        void post(`/${id}/live/audit`, { text: said.text }).catch(() => {})  // logs any figure no card supports
      }
      if (said.interrupted || state.listening) { state.listening = true; await waitForListener() }
      step = { generation: gen - sessionStart.current, heard: turn.id, text: said.text, seconds: said.seconds,
        interrupted: said.interrupted, cut_off: cutOff, listener: state.exchange.splice(0) }
    }
  }

  // Live host: one live session presents every story from the fact cards. The player nudges it on with producer
  // cues after each short chunk, audits every line it speaks for sources and unsupported figures, and lets the
  // listener simply talk over it.
  async function startShow(gen: number) {
    const id = session.current
    setStatus('Connecting your live host…')
    const briefing = await post<{ token: string; prompt: string; voice_id: string | null; speaker: string;
      stories: string[]; max_seconds: number }>(`/${id}/live`, { mode: 'show' })
    if (session.current !== id || generation.current !== gen) return
    const state: NonNullable<typeof showSession.current> = { heardListener: 0, ending: false, startedAt: Date.now(),
      story: briefing.stories[0] ?? '' }
    showSession.current = state
    setHost(briefing.speaker)
    const cue = () => {
      clearTimeout(state.cue)
      state.cue = window.setTimeout(() => {
        if (showSession.current !== state || state.ending) return
        if (Date.now() - state.heardListener < 1500) { cue(); return }  // the listener is talking: wait
        const used = (Date.now() - state.startedAt) / 60000
        state.conversation?.sendUserMessage(`[producer: continue. ${used.toFixed(1)} of ${preferences.minutes} minutes used]`)
      }, 1200)
    }
    const conversation = await Conversation.startSession({
      conversationToken: briefing.token, connectionType: 'webrtc',
      overrides: {
        agent: { prompt: { prompt: briefing.prompt }, firstMessage: "Alright, let's get into today's briefing." },
        ...(briefing.voice_id ? { tts: { voiceId: briefing.voice_id } } : {}),
        conversation: { maxDurationSeconds: briefing.max_seconds },
      },
      onVadScore: ({ vadScore }) => { if (vadScore > 0.6) state.heardListener = Date.now() },
      onModeChange: ({ mode }) => {
        if (showSession.current !== state) return
        if (mode === 'speaking') { clearTimeout(state.cue); setSpeaker(briefing.speaker); setStatus('') }
        else {
          setSpeaker('')
          if (state.ending) void state.conversation?.endSession().catch(() => {})
          else cue()
        }
      },
      onMessage: async ({ message, source }) => {
        const text = message.trim()
        if (showSession.current !== state || !text || text.startsWith('[producer') || text.startsWith('[fact check')) return
        if (source === 'user') {
          state.heardListener = Date.now()
          save({ id: crypto.randomUUID(), speaker: 'Listener', text })
          return
        }
        if (/that'?s the briefing\.?/i.test(text)) state.ending = true
        let cards: { story: number; sources: Segment['sources'] }[] = [], correction: string | null = null, story: string | null = null
        try {
          ({ cards, correction, story } = await post<{ cards: typeof cards; correction: string | null; story: string | null }>(
            `/${id}/live/audit`, { text }))
        } catch { /* show the line without sources */ }
        if (showSession.current !== state) return
        if (story) state.story = story
        if (correction) state.conversation?.sendContextualUpdate(correction)
        const line: Segment = { id: crypto.randomUUID(), story: state.story, speaker: briefing.speaker,
          host: briefing.speaker, guests: [], text, sources: cards.flatMap(card => card.sources) }
        setActive(line)
        save(line)
      },
      onDisconnect: () => {
        if (showSession.current !== state) return
        const finished = state.ending
        end()
        setStatus(finished ? 'Briefing complete.' : 'The live host disconnected. Close the session and try again.')
      },
      onError: message => { if (showSession.current === state) { end(); setStatus('Live host unavailable: ' + message) } },
    })
    if (showSession.current !== state) { void conversation.endSession().catch(() => {}); return }
    state.conversation = conversation
    setStatus('Live · just speak to interrupt.')
  }

  const BACK_TO_SHOW = /\b(carry on|go on|keep going|continue|resume|back to (the )?(briefing|show|panel|news)|that'?s all|that'?s it|thanks?( you)?)\b/i
  const LIVE_IDLE_MS = 12000

  function liveIdle(state: NonNullable<typeof live.current>) {
    clearTimeout(state.idle)
    state.idle = window.setTimeout(() => void finishLive(), LIVE_IDLE_MS)  // quiet after the answer: back to the show
  }

  // Hands the interruption to a live voice agent briefed on this listener and story. Returns false when live
  // conversation isn't available, so the caller answers with a verified segment instead.
  async function startLive(question: string, gen: number): Promise<boolean> {
    const id = session.current
    let briefing: { token: string; prompt: string; voice_id: string | null; speaker: string }
    try { briefing = await post(`/${id}/live`) } catch { return false }
    if (session.current !== id || generation.current !== gen) return true
    const state: NonNullable<typeof live.current> = { gen, transcript: [{ role: 'listener', text: question }], micWasOn: scribe.isConnected }
    live.current = state
    scribe.disconnect()  // the live session takes over the microphone
    try {
      const conversation = await Conversation.startSession({
        conversationToken: briefing.token, connectionType: 'webrtc',
        overrides: { agent: { prompt: { prompt: briefing.prompt }, firstMessage: '' },
          ...(briefing.voice_id ? { tts: { voiceId: briefing.voice_id } } : {}) },
        onModeChange: ({ mode }) => {
          if (live.current !== state) return
          if (mode === 'speaking') { stopHolding(); setSpeaker(briefing.speaker); clearTimeout(state.idle) }
          else { setSpeaker(''); liveIdle(state) }
        },
        onMessage: ({ message, source }) => {
          if (live.current !== state || !message.trim()) return
          const role = source === 'user' ? 'listener' : 'host'
          if (role === 'listener' && state.transcript.at(-1)?.text === message.trim()) return
          state.transcript.push({ role, text: message.trim() })
          save(role === 'listener' ? { id: crypto.randomUUID(), speaker: 'Listener', text: message.trim() }
            : { id: crypto.randomUUID(), story: previousStory.current, speaker: briefing.speaker, host: briefing.speaker,
                guests: [], text: message.trim(), sources: [] })
          if (role === 'listener') { clearTimeout(state.idle); if (BACK_TO_SHOW.test(message)) window.setTimeout(() => void finishLive(), 2500) }
          else setStatus(`${briefing.speaker}: ${message.trim()}`)
        },
        onDisconnect: () => { if (live.current === state) void finishLive() },
        onError: () => { if (live.current === state) void finishLive() },
      })
      if (live.current !== state) { void conversation.endSession().catch(() => {}); return true }
      state.conversation = conversation
      conversation.sendUserMessage(question)
      setLiveWith(briefing.speaker)
      setStatus(`Talking with ${briefing.speaker}. Say "carry on" when you're ready to go back.`)
      return true
    } catch {
      if (live.current === state) live.current = null
      if (state.micWasOn) void microphone()
      return false
    }
  }

  // Ends the live conversation, folds it into the panel and resumes the briefing where it stopped.
  async function finishLive() {
    const state = live.current
    if (!state || state.finishing) return
    state.finishing = true
    clearTimeout(state.idle)
    stopHolding()
    void state.conversation?.endSession().catch(() => {})
    setLiveWith('')
    setSpeaker('')
    const id = session.current
    if (!state.transcript.some(line => line.role === 'host')) {
      // The live session closed before the host said anything (e.g. the agent refused the overrides):
      // answer the question with a verified segment instead, so it never goes unanswered.
      if (live.current === state) live.current = null
      if (!id || session.current !== id || generation.current !== state.gen) return
      if (state.micWasOn) void microphone()
      await next({ question: state.transcript[0].text })
      return
    }
    try {
      if (id && generation.current === state.gen) {
        await post(`/${id}/live/end`, { generation: state.gen - sessionStart.current, transcript: state.transcript })
      }
    } catch (e) { if (generation.current === state.gen) { live.current = null; fail(e); return } }
    if (live.current === state) live.current = null
    if (!id || session.current !== id || generation.current !== state.gen) return
    if (state.micWasOn) void microphone()
    await next()
  }

  // Fits the listener's feed and picks to the chosen minutes, with the chosen number of experts, and shows the
  // planned stories as the agenda.
  async function planBriefing(who: NonNullable<typeof listener>): Promise<DiscussionPlan> {
    const plan = await postJson<DiscussionPlan & { segments: { article_ids: string[] }[] }>('/api/briefing/generate', {
      user_id: who.id, article_ids: who.articleIds, selected_article_ids: who.selectedIds, minutes,
      mode: 'debate', expert_count: experts,
    })
    const ids = plan.segments.map(segment => segment.article_ids[0])
    if (!ids.length) throw new Error('No stories are available for this briefing.')
    const found = await postJson<Record<string, { headline: string; publication: string }>>('/api/recommend/lookup', { article_ids: ids })
      .catch(() => ({} as Record<string, { headline: string; publication: string }>))
    const lineup = ids.map(id => ({ id, title: found[id]?.headline ?? chosenStories.find(s => s.id === id)?.title ?? 'Story',
      topic: found[id]?.publication ?? '', summary: '' }))
    setStories(lineup)
    record.titles = lineup.map(story => story.title)
    return { segments: plan.segments, hosts: plan.hosts, tones: plan.tones } as DiscussionPlan
  }

  async function microphone() {
    const id = session.current
    try {
      const { token } = await post<{ token: string }>(`/${id}/microphone`)
      if (session.current === id) await scribe.connect({ token })
      if (session.current !== id) scribe.disconnect()
    } catch { setStatus('Could not enable the microphone. You can type your question.') }
  }

  const closeLabel = phase === 'playing' && running ? 'Stop briefing' : 'Close briefing'
  const playbackLabel = speaker ? 'Pause briefing' : 'Play briefing'

  return <dialog ref={dialog} className="session-glass" data-setup={phase !== 'playing'} aria-labelledby="room-title"
    style={{ '--launch-x': origin.x + 'px', '--launch-y': origin.y + 'px' } as CSSProperties}
    onCancel={e => { e.preventDefault(); onClose() }}>
    <div className="session-inner">
      <div className="session-intro">
        <header className="session-heading">
          <h1 id="room-title">Your briefing</h1>
          <p className="sr-only">{recorded ? 'Recorded sample' : realtimeMode ? 'Live speech-to-speech' : 'Synthetic narration'}{sample ? ' · Fictional reporting' : ''}</p>
          <div className="session-controls" role="group" aria-label="Briefing controls">
            <button type="button" className="playback-toggle" disabled={!active} aria-label={playbackLabel} title={playbackLabel} onClick={() => {
              const audio = player.current
              if (!audio) return
              if (!audio.paused) audio.pause()
              else void audio.play().catch(() => setStatus('Press play to listen.'))
            }}><Icon name={speaker ? 'pause' : 'play'} /></button>
            <button type="button" autoFocus onClick={onClose} className="room-close" aria-label={closeLabel} title={closeLabel}><Icon name="stop" /></button>
          </div>
        </header>
        {begun && <div className="session-agenda">
          <p className="agenda-label">{complete ? 'Agenda complete' : `Topic ${topic + 1} of ${stories.length}`}</p>
          <ol className="agenda-timeline" aria-label="Topic checkpoints">
            {stories.map((story, index) => <li key={story.id} aria-current={!complete && index === topic ? 'step' : undefined}
              data-complete={complete || index < topic}>
              <span className="checkpoint-dot" aria-hidden="true" />
              <span className="sr-only">Topic {index + 1}: {story.title}. {complete || index < topic ? 'Completed' : index === topic ? 'In discussion' : 'Upcoming'}</span>
            </li>)}
          </ol>
          <h2 className="session-headline">{stories[topic].title}</h2>
        </div>}
      </div>
      <AgentStage audio={player} origin={origin} host={host} guests={guests} speaker={speaker}
        onConfirm={phase !== 'playing' ? confirm : undefined} confirming={phase === 'confirming'}>
        {phase !== 'playing' && <BriefingSetup minutes={minutes} experts={experts} disabled={phase === 'confirming'}
          onMinutes={setMinutes} onExperts={value => { setExperts(value); setMode('panel') }} />}
      </AgentStage>
      <div ref={consolePanel} className="session-console">
      <p role="status" className="player-status">{status}</p>
      {phase === 'setup' && (canGenerateSample || !recorded) && <div className="briefing-setup-options">
        {canGenerateSample && <label className="field">Playback
          <select value={recorded ? 'sample' : 'live'} onChange={event => setRecorded(event.target.value === 'sample')}>
            <option value="sample">Recorded sample</option><option value="live">Generate a personal briefing</option>
          </select>
        </label>}
        {!recorded && <label className="field">Format
          <select value={mode} onChange={event => setMode(event.target.value as Preferences['mode'])}>
            <option value="panel">Expert panel</option><option value="presenter">Presenter</option>
          </select>
        </label>}
      </div>}
      {phase !== 'playing' && <p className="setup-note">{recorded ? 'Recorded preview · fixed audio and two experts. Dial settings apply to generated briefings.'
        : mode === 'presenter' ? 'Presenter mode · host only. Adjust the lower arc to switch to an expert panel.'
        : 'Drag the arcs to adjust. Tap the host to start.'}</p>}
      {saveError && <p role="alert">This browser could not save your briefing. Check the available device storage.</p>}
      <audio ref={player} aria-label="Synthetic briefing audio" hidden
        onPlaying={() => { const line = current.current; if (line) { setBegun(true); setSpeaker(line.speaker); setStatus(''); save(line); introduce() } }}
        onPause={() => setSpeaker('')} onTimeUpdate={introduce} onSeeked={introduce}
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
    {reactions.length > 0 && <ul className="panel-reactions" aria-live="polite">
      {reactions.map(reaction => <li key={reaction.id}><strong>{reaction.who}</strong> {reaction.text}</li>)}
    </ul>}
    {visual && <VisualPanel key={visual.kind + visual.title} visual={visual} leaving={visualLeaving}
      spoken={active?.text ?? ''} pinned={visualPinned} onPin={pinVisual} />}
    <VisualHistory visuals={visualHistory} current={visual} onOpen={earlier => { showVisual(earlier); pinVisual(true) }} />
    {facts.length > 0 && <Sources key={active?.id + ':' + facts[0].claim} cards={facts} compact />}
    {liveWith && <button type="button" className="button" onClick={() => void finishLive()}>Back to the briefing</button>}
    {phase === 'playing' && realtimeMode && running && <button type="button" className="button talk-button"
      onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); holdToTalk(true) }}
      onPointerUp={() => holdToTalk(false)} onPointerCancel={() => holdToTalk(false)}
      onKeyDown={event => { if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) { event.preventDefault(); holdToTalk(true) } }}
      onKeyUp={event => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); holdToTalk(false) } }}>
      Hold to talk</button>}
    {phase === 'playing' && running && !recorded && <form className="flex flex-wrap gap-2" onSubmit={e => {
      e.preventDefault()
      const showing = showSession.current
      if (rt.current?.host && question.trim()) listenerAsks(question.trim())  // the host answers out loud
      else if (showing?.conversation && question.trim()) {  // live host: typed questions go straight into the session
        save({ id: crypto.randomUUID(), speaker: 'Listener', text: question.trim() })
        showing.heardListener = Date.now()
        showing.conversation.sendUserMessage(question.trim())
      } else void ask(question)
      setQuestion('')
    }}>
      {!liveShow && !realtimeMode && <button className="button" type="button" disabled={!session.current || scribe.status === 'connecting'}
        onClick={scribe.isConnected ? scribe.disconnect : microphone}>{scribe.isConnected ? 'Disable microphone' : 'Enable microphone'}</button>}
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
