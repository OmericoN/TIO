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
      sources: [{ id, publisher: 'Source ' + id, claim: 'Quarterly growth', quote: 'Same quarter', language: 'en' }] } })
  })
  await page.route('**/src/realtime.ts*', route => route.fulfill({ contentType: 'application/javascript', body: `
    export class RealtimeAgent {
      static async connect(info, model, mic, handlers) {
        let resolveTurn, words
        return { info, context() {}, close() {}, startListening() {},
          stopListening() { handlers.onListenerText('Which quarter does this cover?') },
          speak(text, seconds, onText) {
            words = text
            onText(text)
            return new Promise(resolve => {
              resolveTurn = resolve
              window.finishPanelTurn = () => resolve({ text, seconds: 8, interrupted: false })
            })
          },
          cancel() { resolveTurn?.({ text: words, seconds: 1, interrupted: true }) },
        }
      }
    }` }))
  await page.goto((process.env.TIO_TEST_ORIGIN || 'http://localhost:5173') + '/demo')
  await page.getByRole('button', { name: 'Select article: Quarterly growth' }).click()
  await page.getByRole('button', { name: 'Start agent discussion' }).click()
  assert.equal(await page.getByLabel('Format').count(), 0)
  await page.getByRole('button', { name: 'Begin briefing' }).click()
  const turnSource = n => page.getByText('Source turn-' + n, { exact: true }).waitFor()
  await turnSource(1)
  const ask = async text => {
    await page.getByRole('textbox', { name: 'Question for the panel' }).fill(text)
    await page.getByRole('button', { name: 'Ask', exact: true }).click()
  }
  await ask('Leila, what changed?')
  await turnSource(2)
  assert.match(await page.locator('.actor[data-speaking="true"]').innerText(), new RegExp(guest.name))
  if (process.env.TIO_TEST_SCREENSHOT) await page.screenshot({ path: process.env.TIO_TEST_SCREENSHOT })
  await page.evaluate(() => window.finishPanelTurn())
  await turnSource(3)
  const talk = page.getByRole('button', { name: 'Interrupt with microphone' })
  await talk.click()
  await talk.press('Space')
  await turnSource(4)
  assert.equal(steps[3].question, 'Which quarter does this cover?')
  assert.equal(steps[3].interrupted, true)
  assert.match(await page.locator('.actor[data-speaking="true"]').innerText(), new RegExp(guest.name))
  const entries = await page.evaluate(async () => (await (await import('/src/briefings.ts')).historyStore('demo'))[0].entries)
  const saved = entries.find(entry => entry.id === 'turn-2')
  assert.equal(saved.text, 'The report compares the same quarter.')
  assert.equal(saved.speaker, guest.name + ' · ' + guest.voice)
  assert.equal(saved.sources[0].publisher, 'Source turn-2')
  await ask('Next story')
  await page.getByText('Briefing complete.', { exact: true }).waitFor()
  assert.equal(await page.locator('vite-error-overlay').count(), 0)
  assert.deepEqual(errors, [])
  console.log('Panel', await page.title(), page.url(), 'typed, microphone deck, sources, history and skip passed')
  await page.close()
}

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] })
  try { await check(browser) } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
