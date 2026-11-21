const assert = require('node:assert/strict')
const { test, mock } = require('node:test')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

// Exercise the real turn handling of a realtime voice, with the WebRTC connection replaced by a fake data channel.
const source = path.resolve(__dirname, '../src/realtime.ts')
const compiled = ts.transpileModule(readFileSync(source, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
globalThis.window = globalThis
globalThis.Audio = class { pause() {} }
const exported = {}
new Function('require', 'exports', compiled)(require, exported)
const { RealtimeAgent } = exported

function voice() {
  const sent = []
  const agent = new RealtimeAgent({ id: 'host', name: 'Tio', role: 'host', voice: 'marin', secret: '' }, { close() {} }, {})
  agent.channel = { readyState: 'open', send: json => sent.push(JSON.parse(json)), close() {} }
  const key = () => sent.findLast(event => event.type === 'response.create').response.metadata.turn
  const server = (type, extra = {}) => agent.receive({ type, response_id: 'reply', ...extra })
  const created = () => agent.receive({ type: 'response.created', response: { id: 'reply', metadata: { turn: key() } } })
  return { agent, sent, server, created }
}

const settle = promise => promise.then(value => ({ value }), error => ({ error }))

test('a turn that runs long is cut off and kept as said, so the briefing carries on', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const { agent, sent, server, created } = voice()
    const spoken = settle(agent.speak('Open the show.', 13))
    created()
    server('output_audio_buffer.started')
    server('response.output_audio_transcript.delta', { delta: 'Welcome to Tío.' })
    mock.timers.tick(38_000)   // the old deadline (13 + 25 seconds) would have failed the briefing here
    mock.timers.tick(1_000)    // 13 × 1.5 + 15 = 34.5 seconds of audio: cut off
    const { value } = await spoken
    assert.equal(value.text, 'Welcome to Tío.')
    assert.equal(value.interrupted, false)
    assert.ok(sent.some(event => event.type === 'response.cancel'))
  } finally { mock.timers.reset() }
})

test('a long turn that finishes on its own is not cut off', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const { agent, sent, server, created } = voice()
    const spoken = settle(agent.speak('Open the show.', 50))
    mock.timers.tick(3_000)
    created()
    server('output_audio_buffer.started')
    mock.timers.tick(80_000)   // well past 50 + 25 seconds from the request, but still within the overrun
    server('output_audio_buffer.stopped')
    const { value } = await spoken
    assert.deepEqual([value.interrupted, value.audioDrained], [false, true])
    assert.ok(!sent.some(event => event.type === 'response.cancel'))
  } finally { mock.timers.reset() }
})

test('a voice that never starts gets another go, and only fails after staying silent three times', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const { agent } = voice()
    for (let attempt = 0; attempt < 2; attempt++) {
      const silent = settle(agent.speak('Say something.', 10))
      mock.timers.tick(20_000)
      assert.equal((await silent).value.interrupted, true)    // reported as interrupted: the speaker keeps the floor
    }
    const third = settle(agent.speak('Say something.', 10))
    mock.timers.tick(20_000)
    assert.match((await third).error.message, /stopped responding/)
  } finally { mock.timers.reset() }
})

test('an error creating the reply, or a reply without audio, ends the turn without waiting', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const { agent, server, created } = voice()
    const failed = settle(agent.speak('Say something.', 10))
    agent.receive({ type: 'error', error: { code: 'rate_limit_exceeded', message: 'Slow down' } })
    assert.equal((await failed).value.interrupted, true)
    const quiet = settle(agent.speak('Say something.', 10))       // the next try produces text but no audio
    created()
    server('response.done', { response: { id: 'reply', status: 'completed' } })
    mock.timers.tick(3_000)
    assert.equal((await quiet).value.interrupted, true)
  } finally { mock.timers.reset() }
})

test('a harmless error about cancelling a finished reply is ignored', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const { agent, server, created } = voice()
    const spoken = settle(agent.speak('Say something.', 10))
    agent.receive({ type: 'error', error: { code: 'response_cancel_not_active' } })
    created()
    server('output_audio_buffer.started')
    server('output_audio_buffer.stopped')
    assert.equal((await spoken).value.interrupted, false)
  } finally { mock.timers.reset() }
})
