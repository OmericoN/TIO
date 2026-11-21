const assert = require('node:assert/strict')
const { test } = require('node:test')
const { readFileSync } = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const ts = require('typescript')

// Use the installed compiler to exercise the same TypeScript helper as the browser.
const source = path.resolve(__dirname, '../src/briefings.ts')
const compiled = ts.transpileModule(readFileSync(source, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText
const helpers = {}
new Function('require', 'exports', compiled)(createRequire(source), helpers)
const { factChecksAt, heardFactChecks, recordedTurns, sampleHeadlines } = helpers

test('history keeps unique nonempty transcripts and citations without audio', () => {
  const turn = recordedTurns(sampleHeadlines)[0]
  const original = { id: 'original', date: '2026-11-20', titles: ['Story', 'Story'], sample: false,
    entries: [turn, { ...turn, id: 'retry', text: '  ' + turn.text.toUpperCase() + '  ', audio: 'large-audio' },
      { id: 'empty', speaker: 'Listener', text: '  ' }, { ...turn, id: 'pending', text: '…' },
      { id: 'question', speaker: 'Listener', text: 'Why does this matter?' }] }
  const duplicate = { ...original, id: 'duplicate', date: '2026-11-21' }
  const distinct = { ...original, id: 'distinct', entries: [{ ...turn, text: 'A different discussion.' }] }
  const saved = helpers.savedBriefings([original, duplicate, distinct, { ...original, id: 'empty', entries: [] }])
  assert.deepEqual(saved.map(row => row.id), ['duplicate', 'distinct'])
  assert.equal(saved[0].entries.length, 2)
  assert.deepEqual(saved[0].titles, ['Story'])
  assert.deepEqual(saved[0].entries[0].sources, turn.sources)
  for (const row of saved) for (const entry of row.entries) {
    assert.ok(!('audio' in entry))
    assert.ok(!('audioUrl' in entry))
  }
  assert.deepEqual(helpers.savedBriefings(saved), saved)
  assert.ok('audioUrl' in original.entries[0], 'live playback data must stay untouched')
})

test('fact checks follow script cues and recompute when seeking backwards', () => {
  const first = { claim: 'Revenue rose.' }, second = { claim: 'Rates held.' }
  const segment = { text: 'Welcome. Revenue rose. Next, Rates held.', sources: [first, second] }
  assert.deepEqual(factChecksAt(segment, 0), [])
  assert.deepEqual(factChecksAt(segment, .4), [])
  assert.deepEqual(factChecksAt(segment, .6), [first])
  assert.deepEqual(factChecksAt(segment, 1), [second])
  assert.deepEqual(factChecksAt(segment, .6), [first])
  assert.deepEqual(factChecksAt(segment, -1), [])
})

test('live fact checks exclude claims skipped or changed by the speaker', () => {
  const first = { claim: 'Revenue rose 7.1%.' }, other = { ...first }
  const cards = [{ claim: 'Margins rose.' }, first, other, { claim: 'Margins narrowed to 4.2% in the quarter.' }]
  assert.deepEqual(heardFactChecks(cards, 'Revenue rose 7.1 percent.'), [first, other])
  assert.deepEqual(heardFactChecks(cards, 'Revenue rose 7.12 percent.'), [])
  assert.deepEqual(heardFactChecks([{ claim: 'Revenue was $27.1 billion.' }], 'Revenue was €27.1 billion.'), [])
})

test('live fact checks accept a paraphrase that keeps every figure', () => {
  const starts = { claim: 'Housing starts rose to 1.31 million in October.' }
  assert.deepEqual(heardFactChecks([starts], 'So starts came in at 1.31 million for October, above forecasts.'), [starts])
  assert.deepEqual(heardFactChecks([starts], 'So starts came in at 1.3 million for October.'), [])
  assert.deepEqual(heardFactChecks([starts], 'And 1.31 million is the number everyone is watching.'), [])
})

test('a claim keeps all its supporting sources and ignores unmapped or empty claims', () => {
  const first = { claim: 'Rates held.' }, other = { claim: 'Rates held.' }
  assert.deepEqual(factChecksAt({ text: 'Rates held.', sources: [first, other, { claim: '' }, { claim: 'Absent' }] }, 2), [first, other])
  assert.deepEqual(factChecksAt({ text: 'An introduction.', sources: [] }, 1), [])
})

test('recorded turns carry stable topic IDs and every citation has a script cue', () => {
  const turns = recordedTurns(sampleHeadlines)
  assert.equal(turns.length, 6)
  for (const turn of turns) {
    assert.ok(sampleHeadlines.some(story => story.id === turn.story_id && story.title === turn.story))
    for (const card of turn.sources) assert.ok(turn.text.toLowerCase().includes(card.claim.toLowerCase()))
  }
})
