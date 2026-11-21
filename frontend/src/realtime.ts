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
    onText?: (text: string) => void; onStart?: () => void; audible?: boolean } | null = null
  private replying = new Set<string>()
  // Asides: quick reactions while someone else holds the floor ("Mm-hmm", "Oh, come on"). They're out-of-band
  // replies, kept out of the agent's conversation, and never mistaken for its turn.
  private asides = new Set<string>()
  private requestingAside = false
  private reacting = false
  private ignoreStops = 0
  private asideAudible = false
  private onAside?: (text: string) => void
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
        if (this.requestingAside) { this.asides.add(id); this.requestingAside = false }
        else if (this.requesting) { this.own.add(id); this.requesting = false } else this.replying.add(id)
        break
      case 'response.output_audio_transcript.delta':
        if (id && this.own.has(id) && this.turn) { this.turn.text += event.delta ?? ''; this.turn.onText?.(this.turn.text) }
        break
      case 'response.output_audio_transcript.done':
        if (id && this.asides.has(id)) { if (event.transcript?.trim()) this.onAside?.(event.transcript.trim()) }
        else if (id && this.own.has(id) && this.turn) this.turn.text = event.transcript ?? this.turn.text
        else if (id && this.replying.has(id) && event.transcript?.trim()) this.handlers.onReply?.(event.transcript.trim())
        break
      case 'response.done':
        if (id && this.asides.has(id) && event.response?.status !== 'completed') this.reacting = false
        if (id && this.own.has(id) && event.response?.status === 'cancelled') this.finish(true)
        break
      case 'output_audio_buffer.started':
        if (id && this.asides.has(id)) { this.asideAudible = true; break }
        if (!id && this.reacting && !this.turn) { this.asideAudible = true; break }
        if (this.turn && !this.turn.audible) {
          this.turn.audible = true
          this.turn.started = performance.now()
          this.turn.onStart?.()
        }
        break
      case 'output_audio_buffer.stopped':
        if (id && this.asides.has(id)) { this.asides.delete(id); this.reacting = false; this.asideAudible = false; break }
        if (!id && this.ignoreStops > 0) { this.ignoreStops--; this.reacting = false; break }  // a cancelled aside
        if (this.turn && (!id || this.own.has(id))) this.finish(false)
        else if (this.replying.size) { this.replying.clear(); this.handlers.onReplyDone?.() }
        else this.reacting = false
        break
      case 'error':
        // A refused aside must not leave the agent stuck "reacting"; turns report their own failures by timing out.
        if (this.requestingAside || (this.reacting && !this.asideAudible)) { this.requestingAside = false; this.reacting = false }
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
  speak(brief: string, maxSeconds: number, onText?: (text: string) => void, onStart?: () => void): Promise<Spoken> {
    this.finish(true)
    this.dropAside()
    return new Promise(resolve => {
      this.turn = { resolve, text: '', started: performance.now(), onText, onStart,
        timer: window.setTimeout(() => this.finish(false), (maxSeconds + 25) * 1000) }
      this.requesting = true
      // The brief goes into the conversation, not into response.instructions: those would replace the agent's
      // persona instructions for this reply, and it would speak as nobody in particular.
      this.note(brief)
      this.send({ type: 'response.create' })
    })
  }

  /** A quick reaction over whoever is talking, at most a few words, in character. Skipped if the agent is busy. */
  react(cue: string, onAside?: (text: string) => void) {
    if (this.turn || this.reacting || this.replying.size) return
    this.reacting = true
    this.requestingAside = true
    this.onAside = onAside
    this.send({ type: 'response.create', response: {
      conversation: 'none', max_output_tokens: 40, output_modalities: ['audio'],
      instructions: `You are ${this.info.name}, on air in a lively news panel, reacting while someone else is talking. `
        + 'Say only a short, natural reaction of one to four words, in character and in your accent: agreement, doubt, '
        + 'a scoff, surprise or a laugh ("Mm-hmm.", "Oh, come on.", "Exactly.", "Hm, not so sure.", "Ha!"). '
        + 'No facts, no figures, no full sentences.',
      input: [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: cue }] }],
    } })
  }

  private dropAside() {
    if (!this.reacting) return
    this.send({ type: 'response.cancel' })
    this.send({ type: 'output_audio_buffer.clear' })
    if (this.asideAudible) this.ignoreStops++  // only a playing aside sends a stop event to swallow
    this.asideAudible = false
    this.reacting = false
    this.requestingAside = false
  }

  /** Stops the agent mid-sentence, e.g. because the listener started talking. */
  cancel() {
    this.dropAside()
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
  answer(question: string, direction?: string) {
    this.note(`LISTENER QUESTION: "${question}". ${direction ?? 'Answer them now, following your instructions and your latest CONTEXT UPDATE.'}`)
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
