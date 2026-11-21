// Experimental realtime panel: one OpenAI Realtime speech-to-speech agent per speaker, connected over WebRTC with an
// ephemeral secret from the backend. Agents speak only when handed a turn brief; the host also hears the microphone
// for push-to-talk transcription.

export type AgentInfo = { id: string; name: string; role: 'host' | 'guest'; voice: string; secret: string }
export type Spoken = { text: string; seconds: number; interrupted: boolean; audioDrained: boolean }

type Handlers = {
  onSpeaking?: (speaking: boolean) => void
  onListenerText?: (text: string) => void
}

type ServerEvent = { type: string; transcript?: string; delta?: string; response_id?: string;
  response?: { id?: string; status?: string; metadata?: { turn?: string } }
  error?: { message?: string; code?: string } }

// A turn that hasn't started speaking after START_SECONDS, or runs well past its planned length, is cut off instead
// of failing the whole briefing: the panel carries on from what was said. Voices often run long on the host's
// openings, so a fixed deadline from the request used to end briefings mid-show.
const START_SECONDS = 20
const overrunSeconds = (maxSeconds: number) => maxSeconds * 1.5 + 15
const SILENT_TURNS = 3  // turns in a row that never start before the voice counts as gone

export class RealtimeAgent {
  private channel!: RTCDataChannel
  private turn: { key: string; responseId?: string; resolve: (spoken: Spoken) => void; reject: (error: Error) => void;
    text: string; started: number; timer: number; maxSeconds: number; audible: boolean
    onText?: (text: string) => void } | null = null
  private silentTurns = 0
  readonly audio = new Audio()

  private microphone: MediaStream | null = null

  private constructor(readonly info: AgentInfo, private pc: RTCPeerConnection, private handlers: Handlers) {}

  static async connect(info: AgentInfo, model: string, microphone: MediaStream | null, handlers: Handlers = {}) {
    const pc = new RTCPeerConnection()
    const agent = new RealtimeAgent(info, pc, handlers)
    agent.audio.autoplay = true
    pc.ontrack = event => { agent.audio.srcObject = event.streams[0] ?? new MediaStream([event.track]) }
    if (microphone) {
      agent.microphone = microphone
      microphone.getAudioTracks().forEach(track => { track.enabled = false; pc.addTrack(track, microphone) })
    }
    else pc.addTransceiver('audio', { direction: 'recvonly' })
    agent.channel = pc.createDataChannel('oai-events')
    agent.channel.onmessage = message => agent.receive(JSON.parse(message.data))
    const opened = new Promise<void>((resolve, reject) => {
      agent.channel.onopen = () => resolve()
      setTimeout(() => reject(new Error(`${info.name} could not connect.`)), 15000)
    })
    await pc.setLocalDescription(await pc.createOffer())
    const response = await fetch(`https://api.openai.com/v1/realtime/calls?model=${encodeURIComponent(model)}`, {
      method: 'POST', body: pc.localDescription!.sdp,
      headers: { Authorization: `Bearer ${info.secret}`, 'Content-Type': 'application/sdp' },
    })
    if (!response.ok) throw new Error(`${info.name} could not connect (HTTP ${response.status}).`)
    await pc.setRemoteDescription({ type: 'answer', sdp: await response.text() })
    await opened
    return agent
  }

  private send(event: object) { if (this.channel.readyState === 'open') this.channel.send(JSON.stringify(event)) }

  private receive(event: ServerEvent) {
    const id = event.response_id ?? event.response?.id
    const turn = this.turn
    if (event.type === 'conversation.item.input_audio_transcription.completed') {
      if (event.transcript?.trim()) this.handlers.onListenerText?.(event.transcript.trim())
      return
    }
    if (!turn) return
    if (event.type === 'error') {
      // An error before this turn's reply exists means the reply couldn't be created. Cancelling a reply that had
      // already finished is harmless.
      if (!turn.responseId && event.error?.code !== 'response_cancel_not_active') {
        console.warn(`${this.info.name} could not start a turn: ${event.error?.message ?? 'unknown error'}`)
        this.stall()
      }
      return
    }
    if (event.type === 'response.created' && event.response?.metadata?.turn === turn.key) {
      turn.responseId = id
      return
    }
    if (!id || id !== turn.responseId) return
    switch (event.type) {
      case 'response.output_audio_transcript.delta':
        turn.text += event.delta ?? ''
        turn.onText?.(turn.text)
        break
      case 'response.output_audio_transcript.done':
        turn.text = event.transcript ?? turn.text
        break
      case 'response.done':
        if (event.response?.status === 'cancelled') this.finish(true)
        else if (event.response?.status === 'failed') {
          console.warn(`${this.info.name} could not complete a turn.`)
          this.stall()
        } else if (!turn.audible) {
          // A reply with no audio leaves nothing to wait for; give a late audio start a moment first.
          clearTimeout(turn.timer)
          turn.timer = window.setTimeout(() => this.stall(), 3000)
        }
        break
      case 'output_audio_buffer.started':
        turn.audible = true
        this.silentTurns = 0
        clearTimeout(turn.timer)
        turn.timer = window.setTimeout(() => this.stall(), overrunSeconds(turn.maxSeconds) * 1000)
        this.handlers.onSpeaking?.(true)
        break
      case 'output_audio_buffer.cleared':
        this.handlers.onSpeaking?.(false)
        break
      case 'output_audio_buffer.stopped':
        this.finish(false, true)
        break
    }
  }

  private finish(interrupted: boolean, audioDrained = false) {
    const turn = this.turn
    if (!turn) return
    this.turn = null
    clearTimeout(turn.timer)
    this.handlers.onSpeaking?.(false)
    turn.resolve({ text: turn.text.trim(), seconds: (performance.now() - turn.started) / 1000, interrupted, audioDrained })
  }

  /** A turn that never started, failed, or ran far too long. One that was heard is kept as said (cut short), so the
   * panel moves on; one that never made a sound is reported as interrupted, so the speaker gets another go. Only a
   * lost connection, or a voice that stays silent turn after turn, ends the briefing. */
  private stall() {
    const turn = this.turn
    if (!turn) return
    if (turn.audible) {
      console.warn(`${this.info.name} ran past the turn's length and was cut off.`)
      this.send({ type: 'response.cancel' })
      this.send({ type: 'output_audio_buffer.clear' })
      this.finish(false, true)
      return
    }
    this.silentTurns += 1
    if (this.channel.readyState !== 'open' || this.silentTurns >= SILENT_TURNS) {
      this.turn = null
      clearTimeout(turn.timer)
      this.handlers.onSpeaking?.(false)
      this.send({ type: 'response.cancel' })
      turn.reject(new Error(`${this.info.name} stopped responding. Try reopening the briefing.`))
      return
    }
    console.warn(`${this.info.name} did not start speaking; trying the turn again.`)
    this.cancel()
  }

  /** Speaks a turn brief in the agent's own words; resolves when its audio has finished playing or was cut off. */
  speak(brief: string, maxSeconds: number, onText?: (text: string) => void): Promise<Spoken> {
    this.finish(true)
    return new Promise((resolve, reject) => {
      const key = crypto.randomUUID()
      this.turn = { key, resolve, reject, text: '', started: performance.now(), onText, maxSeconds, audible: false,
        timer: window.setTimeout(() => this.stall(), START_SECONDS * 1000) }
      // The brief goes into the conversation, not into response.instructions: those would replace the agent's
      // persona instructions for this reply, and it would speak as nobody in particular.
      this.note(brief)
      this.send({ type: 'response.create', response: { metadata: { turn: key } } })
    })
  }

  /** Stops the agent mid-sentence, e.g. because the listener started talking. */
  cancel() {
    if (!this.turn) return
    this.send({ type: 'response.cancel' })
    this.send({ type: 'output_audio_buffer.clear' })
    this.finish(true)
  }

  /** Background context the agent remembers but doesn't answer, e.g. what was just said on the panel. */
  context(text: string) { this.note(text) }

  private note(text: string) {
    this.send({ type: 'conversation.item.create', item: { type: 'message', role: 'system',
      content: [{ type: 'input_text', text }] } })
  }

  /** Open the microphone and start a fresh recording. */
  startListening() {
    this.send({ type: 'input_audio_buffer.clear' })
    this.microphone?.getAudioTracks().forEach(track => { track.enabled = true })
  }

  /** Close the microphone and send what was said for transcription. */
  stopListening() {
    this.microphone?.getAudioTracks().forEach(track => { track.enabled = false })
    this.send({ type: 'input_audio_buffer.commit' })
  }

  close() {
    this.finish(true)
    try { this.channel.close() } catch { /* already closed */ }
    this.pc.close()
    this.audio.srcObject = null
  }
}
