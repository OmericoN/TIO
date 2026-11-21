const assert = require('node:assert/strict')
const { test } = require('node:test')
const { readFileSync } = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const ts = require('typescript')

// Use the installed compiler to exercise the same TypeScript helper as the browser.
const source = path.resolve(__dirname, '../src/briefings.ts')
const compiled = ts.transpileModule(readFileSync(source, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText
const helpers = {}
new Function('require', 'exports', compiled)(createRequire(source), helpers)
const { factChecksAt, heardFactChecks, recordedTurns, sampleHeadlines } = helpers

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
