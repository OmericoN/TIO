// Experimental realtime panel: one OpenAI Realtime speech-to-speech agent per speaker, connected over WebRTC with an
// ephemeral secret from the backend. Agents speak only when handed a turn brief; the host also hears the microphone
// and answers the listener directly.

export type AgentInfo = { id: string; name: string; role: 'host' | 'guest'; voice: string; secret: string }
export type Spoken = { text: string; seconds: number; interrupted: boolean }

type Handlers = {
  onListenerStart?: () => void              // the listener started talking (host only)
  onListenerText?: (text: string) => void   // what they said, transcribed
  onReply?: (text: string) => void          // the host's spoken reply to the listener
  onReplyDone?: () => void                  // that reply finished playing
}

type ServerEvent = { type: string; transcript?: string; delta?: string; response_id?: string;
  response?: { id?: string; status?: string } }

export class RealtimeAgent {
  private channel!: RTCDataChannel
  private own = new Set<string>()           // responses this player asked for (turn briefs)
  private requesting = false
  private turn: { resolve: (spoken: Spoken) => void; text: string; started: number; timer: number;
    onText?: (text: string) => void } | null = null
  private replying = new Set<string>()
  readonly audio = new Audio()

  private microphone: MediaStream | null = null

  private constructor(readonly info: AgentInfo, private pc: RTCPeerConnection, private handlers: Handlers) {}

  static async connect(info: AgentInfo, model: string, microphone: MediaStream | null, handlers: Handlers = {}) {
    const pc = new RTCPeerConnection()
    const agent = new RealtimeAgent(info, pc, handlers)
    agent.audio.autoplay = true
    pc.ontrack = event => { agent.audio.srcObject = event.streams[0] }
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
    switch (event.type) {
      case 'response.created':
        if (!id) break
        if (this.requesting) { this.own.add(id); this.requesting = false } else this.replying.add(id)
        break
      case 'response.output_audio_transcript.delta':
        if (id && this.own.has(id) && this.turn) { this.turn.text += event.delta ?? ''; this.turn.onText?.(this.turn.text) }
        break
      case 'response.output_audio_transcript.done':
        if (id && this.own.has(id) && this.turn) this.turn.text = event.transcript ?? this.turn.text
        else if (id && this.replying.has(id) && event.transcript?.trim()) this.handlers.onReply?.(event.transcript.trim())
        break
      case 'response.done':
        if (id && this.own.has(id) && event.response?.status === 'cancelled') this.finish(true)
        break
      case 'output_audio_buffer.stopped':
        if (this.turn) this.finish(false)
        else if (this.replying.size) { this.replying.clear(); this.handlers.onReplyDone?.() }
        break
      case 'input_audio_buffer.speech_started':
        this.handlers.onListenerStart?.()
        break
      case 'conversation.item.input_audio_transcription.completed':
        if (event.transcript?.trim()) this.handlers.onListenerText?.(event.transcript.trim())
        break
    }
  }

  private finish(interrupted: boolean) {
    const turn = this.turn
    if (!turn) return
    this.turn = null
    clearTimeout(turn.timer)
    turn.resolve({ text: turn.text.trim(), seconds: (performance.now() - turn.started) / 1000, interrupted })
  }

  /** Speaks a turn brief in the agent's own words; resolves when its audio has finished playing or was cut off. */
  speak(brief: string, maxSeconds: number, onText?: (text: string) => void): Promise<Spoken> {
    this.finish(true)
    return new Promise(resolve => {
      this.turn = { resolve, text: '', started: performance.now(), onText,
        timer: window.setTimeout(() => this.finish(false), (maxSeconds + 25) * 1000) }
      this.requesting = true
      // The brief goes into the conversation, not into response.instructions: those would replace the agent's
      // persona instructions for this reply, and it would speak as nobody in particular.
      this.note(brief)
      this.send({ type: 'response.create' })
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

  /** The host answers the listener out loud (spoken questions arrive transcribed; typed ones as they are). */
  answer(question: string) {
    this.note(`LISTENER QUESTION: "${question}". Answer them now, following your instructions and your latest CONTEXT UPDATE.`)
    this.send({ type: 'response.create' })
  }

  /** Push-to-talk: open the microphone and start a fresh recording. */
  startListening() {
    this.send({ type: 'input_audio_buffer.clear' })
    this.microphone?.getAudioTracks().forEach(track => { track.enabled = true })
  }

  /** Push-to-talk released: close the microphone and send what was said for transcription. */
  stopListening() {
    this.microphone?.getAudioTracks().forEach(track => { track.enabled = false })
    this.send({ type: 'input_audio_buffer.commit' })
  }

  /** Lowers or restores this agent's volume, e.g. while checking whether a sound is the listener talking. */
  duck(volume: number) { this.audio.volume = volume }

  close() {
    this.finish(true)
    try { this.channel.close() } catch { /* already closed */ }
    this.pc.close()
    this.audio.srcObject = null
  }
}
