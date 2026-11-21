import { CommitStrategy, Conversation, useScribe } from '@elevenlabs/react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { AgentStage } from './AgentStage'
import { Icon } from './Brand'
import { BriefingSetup } from './BriefingSetup'
import { TalkDeck } from './TalkDeck'
import { Citations } from './Citations'
import { audioUrl, factChecksAt, historyStore, recordedTurns } from './briefings'
import { RealtimeAgent } from './realtime'
import { VisualStack, visualKey } from './Visual'
import type { ShownVisual, Visual } from './Visual'
import type { AgentInfo, Spoken } from './realtime'
import type { Briefing, Card, DiscussionPlan, Entry, Headline, Preferences, Segment } from './briefings'

async function responseError(response: Response, fallback: string): Promise<Error> {
  const detail = (await response.json().catch(() => null))?.detail
  const message = Array.isArray(detail) ? detail.find(issue => typeof issue?.msg === 'string')?.msg : detail
  return new Error(typeof message === 'string' && message.trim() ? message : fallback)
}

async function post<T>(url: string, body = {}): Promise<T> {
  const response = await fetch('/api/panel' + url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) throw await responseError(response, 'Panel unavailable. Check the backend connection.')
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
  origin: { x: number; y: number; context: AudioContext | null }
  // A signed-in listener: the briefing is planned from their recommended feed and picks once they confirm.
  listener?: { id: string; articleIds: string[]; selectedIds: string[] }
  onClose: () => void
}


async function postJson<T>(url: string, body: object): Promise<T> {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw await responseError(response, 'The briefing could not be planned.')
  return response.json()
}

export function PanelPlayer({ owner, stories: chosenStories, preferences, recorded, origin, listener, onClose }: Props) {
  // The agenda: the chosen headlines, replaced by the planned lineup for a signed-in listener.
  const [stories, setStories] = useState<Headline[]>(chosenStories.length ? chosenStories
    : [{ id: 'planning', title: 'Your briefing', topic: '', summary: '' }])
  const [running, setRunning] = useState(true)
  const [status, setStatus] = useState('')
  const [phase, setPhase] = useState<'setup' | 'confirming' | 'playing'>('setup')
  const [minutes, setMinutes] = useState(preferences.minutes)
  const [experts, setExperts] = useState(2)
  const confirmed = useRef(false)
  const [active, setActive] = useState<Segment | null>(null)
  const [begun, setBegun] = useState(false)
  const readyFrom = useRef<{ x: number; y: number } | null>(null)
  const [topic, setTopic] = useState(0)
  const [complete, setComplete] = useState(false)
  const [facts, setFacts] = useState<Card[]>([])
  const [visuals, setVisuals] = useState<ShownVisual[]>([])
  const [liveVisual, setLiveVisual] = useState('')

  // Visuals never fade: each new one is added underneath the earlier ones and stays for the rest of the briefing.
  function showVisual(next: Visual, story: string) {
    const key = visualKey(next)
    setVisuals(shown => shown.some(item => visualKey(item.visual) === key) ? shown : [...shown, { visual: next, story }])
    setLiveVisual(key)
  }
  const [sample, setSample] = useState(recorded)
  const [speaker, setSpeaker] = useState('')
  const [voice, setVoice] = useState<{ speaker: string; stream: MediaStream } | null>(null)
  const [host, setHost] = useState('Main Host')
  const [guests, setGuests] = useState<string[]>([])
  const [saveError, setSaveError] = useState(false)
  const [record] = useState<Briefing>(() => ({ id: crypto.randomUUID(), date: new Date().toISOString(), titles: stories.map(s => s.title), entries: [], sample: recorded }))
  const sampleQueue = useRef<Segment[]>([])
  const previousStory = useRef('')
  const cueContext = useRef<AudioContext | null>(origin.context)
  const dialog = useRef<HTMLDialogElement>(null)
  const consolePanel = useRef<HTMLDivElement>(null)
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
  const [talkMic, setTalkMic] = useState<MediaStream | null>(null)
  const [talkPhase, setTalkPhase] = useState<'idle' | 'recording' | 'waiting'>('idle')
  const realtimeMode = !recorded
  const rt = useRef<{ agents: Map<string, RealtimeAgent>; host?: RealtimeAgent; microphone: MediaStream | null;
    current?: RealtimeAgent; listening: boolean; questions: string[];
    transcriptionTimer?: number; release?: () => void } | null>(null)

  function resumePanel(state: NonNullable<typeof rt.current>) {
    if (rt.current !== state) return
    clearTimeout(state.transcriptionTimer)
    state.listening = false
    state.release?.()
    state.release = undefined
    setTalkPhase('idle')
    setStatus('')
  }

  function toggleTalk() {
    const state = rt.current
    if (!state?.host || !state.microphone) return
    if (talkPhase === 'recording') {
      setTalkPhase('waiting')
      setStatus('Thinking about your question…')
      state.transcriptionTimer = window.setTimeout(() => resumePanel(state), 8000)
      state.host.stopListening()
    } else if (!state.listening) {
      state.listening = true
      state.current?.cancel()
      void cueContext.current?.resume().catch(() => {})
      state.host.startListening()
      setTalkPhase('recording')
      setStatus('Listening… tap the deck again to send.')
    }
  }

  function listenerAsks(text: string) {
    const state = rt.current
    if (!state?.host) return
    state.questions.push(text.slice(0, 2000))
    state.current?.cancel()
    save({ id: crypto.randomUUID(), speaker: 'Listener', text })
    resumePanel(state)
    setTalkPhase('waiting')
    setStatus('Finding the answer in your sources…')
  }


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
    if (segment || !realtimeMode) setFacts(segment ? factChecksAt(segment, 0) : [])
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

  function closeRealtime() {
    const panel = rt.current
    if (panel) {
      rt.current = null
      clearTimeout(panel.transcriptionTimer)
      panel.release?.()
      panel.agents.forEach(agent => agent.close())
      panel.microphone?.getTracks().forEach(track => track.stop())
    }
  }

  function end() {
    stopHolding()
    closeRealtime()
    setTalkMic(null)
    setTalkPhase('idle')
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
    onError: () => setStatus('Microphone unavailable.'),
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
      closeRealtime()
      scribe.disconnect()
      if (session.current) void fetch('/api/panel/' + session.current, { method: 'DELETE' }).catch(() => {})
      session.current = ''
    }
  }, [])

  async function confirm() {
    if (confirmed.current) return
    confirmed.current = true
    readyFrom.current = null
    setBegun(false)
    setTopic(0)
    setFacts([])
    setPhase('confirming')
    const gen = generation.current
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const dials = dialog.current?.querySelector('.setup-dials')
      const digest = dials?.animate([{ transform: 'scale(1)', opacity: 1 }, { transform: 'scale(.36)', opacity: 0 }],
        { duration: 280, easing: 'cubic-bezier(.77,0,.175,1)', fill: 'forwards' })
      await digest?.finished.catch(() => {})
    }
    if (generation.current !== gen) return
    setPhase('playing')
    void start()
  }

  function beginPlayback() {
    if (!readyFrom.current) {
      const rect = dialog.current?.querySelector('.host-body')?.getBoundingClientRect()
      if (rect) readyFrom.current = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
    }
    setBegun(true)
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
      let agenda = stories
      let lineup: object = { expert_count: experts, story_ids: agenda.map(s => s.id) }
      if (listener) {
        setStatus('Planning your briefing…')
        const { plan, plannedStories } = await planBriefing(listener)
        if (generation.current !== gen) return
        agenda = plannedStories
        setStories(agenda)
        record.titles = agenda.map(story => story.title)
        lineup = { briefing_plan: plan, expert_count: experts }
      }
      const result = await post<{ id: string; sample: boolean }>('', { ...preferences, mode: 'panel', minutes, ...lineup })
      if (generation.current !== gen) {
        await fetch('/api/panel/' + result.id, { method: 'DELETE' })
        return
      }
      session.current = result.id
      sessionStart.current = gen
      if (!realtimeMode) void loadHolding(result.id)
      record.sample = result.sample
      setSample(result.sample)
      if (realtimeMode) await startRealtime(gen, agenda)
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
  // listener talks, the active turn stops and the question goes through the normal panel turn loop.
  async function startRealtime(gen: number, agenda: Headline[]) {
    const id = session.current
    setStatus('Connecting the panel…')
    const { model, agents } = await post<{ model: string; agents: AgentInfo[] }>(`/${id}/realtime/agents`)
    let microphone: MediaStream | null = null
    try { microphone = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }) }
    catch { setStatus('Microphone unavailable.') }
    if (session.current !== id || generation.current !== gen) { microphone?.getTracks().forEach(t => t.stop()); return }
    const state: NonNullable<typeof rt.current> = { agents: new Map(), microphone, listening: false, questions: [] }
    rt.current = state
    const hostInfo = agents.find(agent => agent.role === 'host')!
    const connected = await Promise.all(agents.map(info => RealtimeAgent.connect(info, model,
      info.role === 'host' ? microphone : null, {
        onSpeaking: speaking => {
          if (rt.current !== state) return
          setSpeaker(current => speaking ? info.name : current === info.name ? '' : current)
          if (speaking) {
            const stream = state.agents.get(info.id)?.audio?.srcObject
            setVoice(stream instanceof MediaStream ? { speaker: info.name, stream } : null)
          } else setVoice(current => current?.speaker === info.name ? null : current)
        },
        ...(info.role === 'host' ? {
          onListenerText: (text: string) => {
            if (rt.current !== state || !state.listening) return
            clearTimeout(state.transcriptionTimer)
            if (!text.split(/\s+/).some(word => /\w/.test(word))) { resumePanel(state); return }
            listenerAsks(text)
          },
        } : {}),
      })))
    if (rt.current !== state) { connected.forEach(agent => agent.close()); return }
    connected.forEach(agent => state.agents.set(agent.info.id, agent))
    state.host = state.agents.get(hostInfo.id)
    setTalkMic(microphone)
    setHost(hostInfo.name)
    console.info('Realtime panel voices', Object.fromEntries(agents.map(a => [a.name, a.voice])))
    const liveStatus = `Live · ${agents.length} realtime ${agents.length === 1 ? 'voice' : 'voices'}${microphone ? '' : ' · Microphone unavailable'}`
    setStatus(liveStatus)
    const waitForListener = () => state.listening
      ? new Promise<void>(resolve => { state.release = resolve })
      : Promise.resolve()

    let step: object = { generation: gen - sessionStart.current }
    while (rt.current === state) {
      type Turn = { done?: boolean; context?: string | null; id: string; speaker_id: string; speaker: string;
        guests: string[]; story: string; story_id: string; brief: string; max_seconds: number; sources: Segment['sources']
        visual: Visual | null }
      await waitForListener()
      if (rt.current !== state) return
      const question = state.questions.shift()
      const turn = await post<Turn>(`/${id}/realtime/next`, { ...step, question })
      if (rt.current !== state) return
      if (turn.context) state.host?.context(turn.context)
      // Every agent hears the panel: each spoken line goes to all of them, so guests can react to each other.
      if (turn.done) { setComplete(true); end(); setStatus('Briefing complete.'); return }
      const agent = state.agents.get(turn.speaker_id)
      if (!agent) throw new Error(`No agent connected for ${turn.speaker}.`)
      if (previousStory.current !== turn.story) {
        setGuests([])
        setFacts([])
        if (previousStory.current) await storyCue(cueContext.current ??= new AudioContext())
        previousStory.current = turn.story
      }
      while (rt.current === state && state.listening) await waitForListener()
      if (rt.current !== state) return
      if (!question && state.questions.length) {
        step = { generation: gen - sessionStart.current, heard: turn.id, interrupted: true }
        continue
      }
      setTalkPhase('idle')
      setStatus(liveStatus)
      setGuests(turn.guests)
      beginPlayback()
      const exactTopic = agenda.findIndex(story => story.id === turn.story_id)
      const nextTopic = exactTopic >= 0 ? exactTopic : agenda.findIndex(story => story.title === turn.story)
      if (nextTopic >= 0) setTopic(nextTopic)
      if (turn.visual) showVisual(turn.visual, turn.story)
      const voice = agents.find(a => a.id === turn.speaker_id)?.voice
      const line: Segment = { id: turn.id, story: turn.story, speaker: voice ? `${turn.speaker} · ${voice}` : turn.speaker, host: hostInfo.name,
        guests: turn.guests, text: '…', sources: turn.sources }
      setActive(line)
      if (turn.sources.length) setFacts(turn.sources)  // the facts this line draws on are listed as soon as it starts
      state.current = agent
      // The transcript appears as the agent improvises it, word by word, rather than as a finished script.
      const said = await agent.speak(turn.brief, turn.max_seconds, text => setActive({ ...line, text }))
      state.current = undefined
      if (rt.current !== state) return
      if (turn.visual) setLiveVisual('')
      if (said.text && !said.interrupted) {
        for (const other of state.agents.values()) {
          if (other !== agent) other.context(`ON AIR (context only): ${turn.speaker} said: "${said.text}"`)
        }
        const spoken = { ...line, text: said.text }
        setActive(spoken)
        save(spoken)
        void post(`/${id}/live/audit`, { text: said.text }).catch(() => {})  // logs any figure no card supports
      }
      while (rt.current === state && state.listening) await waitForListener()
      step = { generation: gen - sessionStart.current, heard: turn.id, text: said.text, seconds: said.seconds,
        interrupted: said.interrupted }
    }
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
  async function planBriefing(who: NonNullable<typeof listener>) {
    const plan = await postJson<DiscussionPlan & { segments: { article_ids: string[] }[] }>('/api/briefing/generate', {
      user_id: who.id, article_ids: who.articleIds, selected_article_ids: who.selectedIds, minutes,
      mode: 'debate', expert_count: experts,
    })
    const ids = plan.segments.map(segment => segment.article_ids[0])
    if (!ids.length) throw new Error('No stories are available for this briefing.')
    if (plan.hosts.length < experts) throw new Error('Too few matching experts for this panel. Try a smaller panel or different stories.')
    const found = await postJson<Record<string, { headline: string; publication: string }>>('/api/recommend/lookup', { article_ids: ids })
      .catch(() => ({} as Record<string, { headline: string; publication: string }>))
    const plannedStories = ids.map(id => ({ id, title: found[id]?.headline ?? chosenStories.find(s => s.id === id)?.title ?? 'Story',
      topic: found[id]?.publication ?? '', summary: '' }))
    return { plannedStories, plan: { segments: plan.segments, hosts: plan.hosts, tones: plan.tones } }
  }

  async function microphone() {
    const id = session.current
    try {
      const { token } = await post<{ token: string }>(`/${id}/microphone`)
      if (session.current === id) await scribe.connect({ token })
      if (session.current !== id) scribe.disconnect()
    } catch { setStatus('Could not enable the microphone.') }
  }

  const closeLabel = phase === 'playing' && running ? 'Stop briefing' : 'Close briefing'
  const playbackLabel = speaker ? 'Pause briefing' : 'Play briefing'
  const loading = phase !== 'setup' && !begun && running

  return <dialog ref={dialog} className="session-glass" data-setup={phase !== 'playing' || loading} aria-labelledby="room-title"
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
          <ol className="agenda-timeline" aria-label="Topic checkpoints"
            style={{ '--topic-progress': complete ? 1 : topic / Math.max(1, stories.length - 1) } as CSSProperties}>
            {stories.map((story, index) => <li key={story.id} aria-current={!complete && index === topic ? 'step' : undefined}
              data-complete={complete || index < topic}>
              <span className="checkpoint-dot" aria-hidden="true" />
              <span className="sr-only">Topic {index + 1}: {story.title}. {complete || index < topic ? 'Completed' : index === topic ? 'In discussion' : 'Upcoming'}</span>
            </li>)}
          </ol>
        </div>}
      </div>
      <AgentStage origin={origin} host={host} guests={guests} speaker={speaker} speech={voice?.speaker === speaker ? voice.stream : null}
        readyFrom={readyFrom.current}
        orbPhase={phase === 'setup' ? 'setup' : begun ? 'ready' : 'loading'}
        onConfirm={phase !== 'playing' ? confirm : undefined} confirming={phase === 'confirming'}>
        {phase !== 'playing' && <BriefingSetup minutes={minutes} experts={experts} disabled={phase === 'confirming'}
          onMinutes={setMinutes} onExperts={setExperts} />}
      </AgentStage>
      {begun && <h2 className="session-headline">{stories[topic].title}</h2>}
      <div ref={consolePanel} className="session-console">
      <p role="status" className={loading && status !== 'Press play to listen.' ? 'player-status sr-only' : 'player-status'}>{status || (loading ? 'Loading briefing…' : '')}</p>
      {recorded && (phase !== 'playing' || loading) && <p className={loading ? 'setup-note invisible' : 'setup-note'}>Recorded preview · fixed audio and two experts. Dial settings apply to generated briefings.</p>}
      {saveError && <p role="alert">This browser could not save your briefing. Check the available device storage.</p>}
      <audio ref={player} aria-label="Synthetic briefing audio" hidden
        onPlaying={() => { const line = current.current; if (line) { beginPlayback(); setSpeaker(line.speaker); setStatus(''); save(line); introduce() } }}
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
    <VisualStack visuals={visuals} live={liveVisual} spoken={active?.text ?? ''} />
    {phase === 'playing' && begun && (running || complete) && <Citations speaking={active?.sources ?? []} cited={facts} story={active?.story ?? stories[topic]?.title ?? ''} />}
    {liveWith && <button type="button" className="button" onClick={() => void finishLive()}>Back to the briefing</button>}
    {phase === 'playing' && running && !recorded && <>
      {!realtimeMode && <button className="button" type="button" disabled={!session.current || scribe.status === 'connecting'}
        onClick={scribe.isConnected ? scribe.disconnect : microphone}>{scribe.isConnected ? 'Disable microphone' : 'Enable microphone'}</button>}
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
    </>}
      </div>
    </div>
    {phase === 'playing' && begun && realtimeMode && running && <TalkDeck microphone={talkMic} context={cueContext.current}
      recording={talkPhase === 'recording'} disabled={!talkMic || talkPhase === 'waiting'} onToggle={toggleTalk} />}
  </dialog>
}
