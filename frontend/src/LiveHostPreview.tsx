import { ConversationProvider, useConversationControls, useConversationStatus } from '@elevenlabs/react'
import { useState } from 'react'

function LiveHostControls() {
  const { startSession, endSession } = useConversationControls()
  const { status } = useConversationStatus()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const connected = status === 'connected'

  async function connect() {
    setError('')
    setLoading(true)
    try {
      const response = await fetch('/api/voice/session', { method: 'POST' })
      if (response.status === 503) {
        setError('Set the ElevenLabs API key and agent ID in FastAPI, then restart it.')
        return
      }
      if (!response.ok) {
        setError('Could not get a voice session from FastAPI. Check that the backend is running.')
        return
      }
      const { token, system_prompt: prompt } = await response.json() as {
        token: string
        system_prompt: string
      }
      if (!token || !prompt) {
        setError('FastAPI returned an incomplete voice session.')
        return
      }
      await startSession({
        conversationToken: token,
        connectionType: 'webrtc',
        overrides: { agent: { prompt: { prompt } } },
      })
    } catch {
      setError('Could not start the preview. Check FastAPI and your microphone permissions.')
    } finally {
      setLoading(false)
    }
  }

  const connectionError = status === 'error'
    ? 'Voice connection failed. Check microphone access and the agent’s System prompt override setting.'
    : ''

  return (
    <section className="mt-8">
      <h2 className="text-xl font-semibold">Live host preview</h2>
      <p className="mt-2">Start a session to speak with the AI host. Your browser will request microphone access.</p>
      <button
        type="button"
        className="mt-4 rounded bg-black px-4 py-2 text-white disabled:opacity-50"
        onClick={connected ? () => endSession() : connect}
        disabled={loading || status === 'connecting'}
      >
        {connected ? 'End session' : loading || status === 'connecting' ? 'Connecting…' : 'Start voice preview'}
      </button>
      <p role="status" className="mt-2">{connected ? 'Connected' : ''}</p>
      {(error || connectionError) && <p role="alert" className="mt-2">{error || connectionError}</p>}
    </section>
  )
}

export function LiveHostPreview() {
  return (
    <ConversationProvider>
      <LiveHostControls />
    </ConversationProvider>
  )
}
