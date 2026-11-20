import { CommitStrategy, useScribe } from '@elevenlabs/react'
import { useEffect, useRef, useState } from 'react'

type Card = { id: string; publisher: string; language: string; url: string | null; quote: string; claim: string }
type Segment = { id: string; story: string; speaker: string; text: string; sources: Card[]; audio: string }

async function post<T>(url: string, body = {}): Promise<T> {
  const response = await fetch('/api/panel' + url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error((await response.json()).detail || 'Panel request failed.')
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

export function PanelPlayer() {
  const [profile, setProfile] = useState('Explain what changed and why it matters to a general news listener.')
  const [mode, setMode] = useState('panel')
  const [minutes, setMinutes] = useState(4)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [active, setActive] = useState<Segment | null>(null)
  const [archive, setArchive] = useState<Segment[]>([])
  const [sample, setSample] = useState(false)
  const [question, setQuestion] = useState('')
  const player = useRef<HTMLAudioElement>(null)
  const session = useRef('')
  const generation = useRef(0)
  const sessionStart = useRef(0)
  const pendingInterrupt = useRef<Promise<unknown> | null>(null)
  const current = useRef<Segment | null>(null)
  const finished = useRef<{ heard: string; seconds: number; fully_heard: boolean } | null>(null)
  const button = 'rounded-lg bg-[#263a33] px-3.5 py-2 text-sm font-medium text-white hover:bg-[#354c42] disabled:opacity-50'

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
    audio.src = 'data:audio/mpeg;base64,' + segment.audio
    void audio.play().catch(() => { if (current.current === segment) setStatus('Press play to listen.') })
  }

  function end() {
    generation.current++
    if (session.current) void fetch('/api/panel/' + session.current, { method: 'DELETE' }).catch(() => {})
    session.current = ''
    pendingInterrupt.current = null
    scribe.disconnect()
    show(null)
    setRunning(false)
  }

  function fail(error: unknown) {
    end()
    setStatus(error instanceof Error ? error.message : 'Panel unavailable.')
  }

  async function next(body: object = {}) {
    const id = session.current, gen = generation.current
    if (!id) return
    setStatus('Preparing a verified segment…')
    try {
      const result = await streamTurn(id, { ...body, generation: gen - sessionStart.current }, message => {
        if (session.current === id && generation.current === gen) setStatus(message)
      })
      if (session.current !== id || generation.current !== gen) return
      if ('done' in result) { end(); setStatus('Briefing complete.'); return }
      setStatus('')
      show(result)
    } catch (e) { if (id === session.current && gen === generation.current) fail(e) }
  }

  function interrupt() {
    if (!session.current || pendingInterrupt.current) return
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
    interrupt()
    const gen = generation.current
    try {
      await pendingInterrupt.current
      if (generation.current !== gen) return
      pendingInterrupt.current = null
      await next({ question: text })
    } catch (e) { if (generation.current === gen) fail(e) }
  }

  const scribe = useScribe({
    modelId: 'scribe_v3_live', commitStrategy: CommitStrategy.VAD,
    microphone: { echoCancellation: true, noiseSuppression: true },
    onPartialTranscript: ({ text }) => { if (text.trim()) interrupt() },
    onCommittedTranscript: ({ text }) => { void ask(text) },
    onError: () => setStatus('Microphone unavailable. You can type your question.'),
  })

  useEffect(() => () => {
    generation.current++
    player.current?.pause()
    if (session.current) void fetch('/api/panel/' + session.current, { method: 'DELETE' }).catch(() => {})
    session.current = ''
  }, [])

  async function start() {
    const gen = ++generation.current
    show(null)
    setArchive([])
    finished.current = null
    setSample(false)
    setRunning(true)
    setStatus('Preparing the rundown…')
    try {
      const result = await post<{ id: string; sample: boolean }>('', { profile, mode, minutes })
      if (generation.current !== gen) {
        await fetch('/api/panel/' + result.id, { method: 'DELETE' })
        return
      }
      session.current = result.id
      sessionStart.current = gen
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

  return <section className="mt-6 space-y-4">
    <h2 className="text-xl font-semibold">Personalized audio briefing</h2>
    <label className="block">Listener profile
      <textarea className="block w-full rounded border p-2" value={profile} maxLength={3000}
        disabled={running} onChange={e => setProfile(e.target.value)} />
    </label>
    <div className="flex flex-wrap items-center gap-3">
      <label>Format <select disabled={running} value={mode} onChange={e => setMode(e.target.value)}>
        <option value="panel">Expert panel</option><option value="presenter">Presenter</option>
      </select></label>
      <label>Minutes <input className="w-14 border" type="number" min="1" max="15" value={minutes}
        disabled={running} onChange={e => setMinutes(Number(e.target.value))} /></label>
      <button className={button} disabled={!running && (!profile.trim() || minutes < 1 || minutes > 15)}
        onClick={running ? () => { end(); setStatus('Stopped.') } : start}>{running ? 'Stop' : 'Start briefing'}</button>
    </div>
    <p>Synthetic narration{sample ? ' · Fictional sample reporting' : ''}</p>
    <audio ref={player} controls className="w-full" onEnded={() => {
      const heard = current.current
      if (!session.current || !heard) return
      const audio = player.current!
      let seconds = 0
      for (let i = 0; i < audio.played.length; i++) seconds += audio.played.end(i) - audio.played.start(i)
      finished.current = { heard: heard.id, seconds, fully_heard: seconds >= audio.duration - 0.15 }
      setArchive(a => [...a, heard])
      show(null)
      void next(finished.current)
    }} />
    {active && <article><h3 className="font-semibold">{active.story}</h3><p>{active.speaker} — {active.text}</p>
      {active.sources.map((s, i) => <blockquote key={i} className="my-2 border-l-2 pl-3">
        <strong>{s.claim}</strong><p lang={s.language}>{s.quote}</p><footer>{s.url
          ? <a href={s.url} target="_blank" rel="noreferrer">{s.publisher}</a> : s.publisher}</footer>
      </blockquote>)}</article>}
    {running && <form className="flex flex-wrap gap-2" onSubmit={e => {
      e.preventDefault(); void ask(question); setQuestion('')
    }}>
      <button className={button} type="button" disabled={!session.current || scribe.status === 'connecting'}
        onClick={scribe.isConnected ? scribe.disconnect : microphone}>{scribe.isConnected ? 'Disable microphone' : 'Enable microphone'}</button>
      <input className="grow rounded border p-2" aria-label="Question for the panel" placeholder="Ask the panel"
        value={question} maxLength={1000} onChange={e => setQuestion(e.target.value)} />
      <button className={button} disabled={!question.trim() || !session.current}>Ask</button>
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
    <p role="status">{status}</p>
    {!running && archive.length > 0 && <ol>{archive.map(s => <li key={s.id}>
      <button className="text-left underline" onClick={() => show(s)}>Replay {s.speaker} · {s.text}</button>
    </li>)}</ol>}
  </section>
}
