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
const { factChecksAt, recordedTurns, sampleHeadlines } = helpers

test('fact checks follow script cues and recompute when seeking backwards', () => {
  const first = { claim: 'Revenue rose.' }, second = { claim: 'Rates held.' }
  const segment = { text: 'Welcome. Revenue rose. Next, Rates held.', sources: [first, second] }
  assert.deepEqual(factChecksAt(segment, 0), [])
  assert.deepEqual(factChecksAt(segment, .4), [first])
  assert.deepEqual(factChecksAt(segment, 1), [second])
  assert.deepEqual(factChecksAt(segment, .4), [first])
  assert.deepEqual(factChecksAt(segment, -1), [])
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
