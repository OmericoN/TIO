// Run with Vite on localhost:5173, Playwright and Chrome. All providers are stubbed.
const assert = require('node:assert/strict')
const { chromium } = require('playwright')

async function check(browser) {
  const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1200, height: 850 } })
  const host = { id: 'tio', name: 'Tio', role: 'host', voice: 'marin' }
  const guest = { id: 'leila', name: 'Leila Grant', role: 'guest', voice: 'sage' }
  const agents = [host, guest]
  const steps = [], errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })
  await page.route('**/api/recommend/catalog', route => route.fulfill({ json: { users: [] } }))
  await page.route('**/api/panel/stories', route => route.fulfill({ json: { sample: false,
    stories: [{ id: 'story', title: 'Quarterly growth', summary: 'Test coverage', topic: 'Economy' }] } }))
  await page.route('**/api/panel', route => {
    assert.equal(route.request().postDataJSON().mode, 'panel')
    return route.fulfill({ json: { id: 'test', sample: false } })
  })
  await page.route('**/test/realtime/agents', route => route.fulfill({ json: { model: 'fixture', agents } }))
  await page.route('**/test/live/audit', route => route.fulfill({ json: {} }))
  await page.route('**/api/panel/test', route => route.fulfill({ status: 204 }))
  await page.route('**/test/realtime/next', route => {
    const step = route.request().postDataJSON()
    steps.push(step)
    if (step.question === 'Next story') return route.fulfill({ json: { done: true } })
    const speaker = step.question ? guest : host
    const id = 'turn-' + steps.length
    return route.fulfill({ json: { id, speaker_id: speaker.id, speaker: speaker.name, story_id: 'story',
      story: 'Quarterly growth', guests: [guest.name], max_seconds: 15,
      brief: step.question ? 'The report compares the same quarter.' : 'The panel is ready.',
      sources: [{ id, publisher: 'Source ' + id, claim: 'The report compares the same quarter.', quote: 'Same quarter', language: 'en' }],
      visual: step.question === 'Leila, what changed?' ? {
        kind: 'flow', title: 'How the comparison works', sources: ['Quarterly report'],
        steps: ['Compare the same quarter'], step_cards: [id],
        evidence: { [id]: { point: 'Compare the same quarter', quotes: [
          { publisher: 'Quarterly report', quote: 'The report uses the prior year quarter.' },
        ] } },
      } : null } })
  })
  await page.route('**/src/realtime.ts*', route => route.fulfill({ contentType: 'application/javascript', body: `
    export class RealtimeAgent {
      static async connect(info, model, mic, handlers) {
        let resolveTurn, words
        return { info, context() {}, close() {}, startListening() {},
          stopListening() { handlers.onListenerText(window.panelQuestion) },
          speak(text, seconds, onText) {
            handlers.onSpeaking(true)
            words = text
            onText(text)
            return new Promise(resolve => {
              resolveTurn = resolve
              window.finishPanelTurn = () => {
                handlers.onSpeaking(false)
                resolve({ text, seconds: 8, interrupted: false, audioDrained: true })
              }
            })
          },
          cancel() {
            handlers.onSpeaking(false)
            resolveTurn?.({ text: words, seconds: 1, interrupted: true, audioDrained: false })
          },
        }
      }
    }` }))
  await page.goto((process.env.TIO_TEST_ORIGIN || 'http://localhost:5173') + '/demo')
  await page.getByRole('button', { name: 'Select article: Quarterly growth' }).click()
  await page.getByRole('button', { name: 'Start agent discussion' }).click()
  assert.equal(await page.getByLabel('Format').count(), 0)
  await page.getByRole('button', { name: 'Begin briefing' }).click()
  const activeSpeaker = name => page.locator('.actor[data-speaking="true"]').filter({ hasText: name }).waitFor()
  const talk = page.getByRole('button', { name: 'Interrupt with microphone' })
  const ask = async text => {
    await page.evaluate(question => { window.panelQuestion = question }, text)
    await talk.click()
    await talk.press('Space')
  }
  await activeSpeaker(host.name)
  assert.equal(await page.getByRole('textbox', { name: 'Question for the panel' }).count(), 0)
  await ask('Leila, what changed?')
  await activeSpeaker(guest.name)
  assert.equal(steps[1].question, 'Leila, what changed?')
  const visual = page.getByRole('figure', { name: 'How the comparison works' })
  await visual.getByRole('button', { name: 'Compare the same quarter' }).click()
  await visual.getByText('The report uses the prior year quarter.', { exact: false }).waitFor()
  await visual.getByRole('button', { name: 'Keep' }).click()
  assert.equal(await page.getByRole('complementary', { name: 'Fact check' }).count(), 0)
  await page.evaluate(() => window.finishPanelTurn())
  await page.getByRole('complementary', { name: 'Fact check' }).waitFor()
  if (process.env.TIO_TEST_SCREENSHOT) await page.screenshot({ path: process.env.TIO_TEST_SCREENSHOT })
  await activeSpeaker(host.name)
  assert.equal(await visual.getByRole('button', { name: 'Kept' }).getAttribute('aria-pressed'), 'true')
  await ask('Which quarter does this cover?')
  await activeSpeaker(guest.name)
  assert.equal(steps[3].question, 'Which quarter does this cover?')
  assert.equal(steps[3].interrupted, true)
  const entries = await page.evaluate(async () => (await (await import('/src/briefings.ts')).historyStore('demo'))[0].entries)
  const saved = entries.find(entry => entry.id === 'turn-2')
  assert.equal(saved.text, 'The report compares the same quarter.')
  assert.equal(saved.speaker, guest.name + ' · ' + guest.voice)
  assert.equal(saved.sources[0].publisher, 'Source turn-2')
  await ask('Next story')
  await page.getByText('Briefing complete.', { exact: true }).waitFor()
  assert.equal(await page.locator('vite-error-overlay').count(), 0)
  assert.deepEqual(errors, [])
  console.log('Panel', await page.title(), page.url(), 'named expert, microphone deck, sources, history and skip passed')
  await page.close()
}

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] })
  try { await check(browser) } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
