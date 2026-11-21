"""Run with Vite on localhost:5173 and Python Playwright + Edge installed.

All panel requests are mocked; this check makes no provider calls.
"""
import math

from playwright.sync_api import expect, sync_playwright

def point(slider, angle, lower=False):
    rect = slider.bounding_box()
    radians = math.radians(angle)
    return (rect['x'] + (140 + 116 * math.cos(radians)) / 280 * rect['width'],
            rect['y'] + (116 * math.sin(radians) + (0 if lower else 140)) / 140 * rect['height'])


with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge')
    for width in (320, 524, 1440):
        page = browser.new_page(viewport={'width': width, 'height': 850}, reduced_motion='reduce', has_touch=width == 320)
        starts, turns, errors = [], [], []
        opening_route = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        feed = [{'id': 'story', 'title': 'A briefing topic', 'summary': 'Test summary', 'topic': 'Economy'}]
        if width == 524:
            feed.extend({**feed[0], 'id': f'story-{i}', 'title': f'Topic {i} headline'} for i in (2, 3))
        page.route('**/api/panel/stories', lambda r: r.fulfill(json={'sample': False, 'stories': feed}))

        def start(route):
            starts.append(route.request.post_data_json)
            route.fulfill(json={'id': 'test', 'sample': False})

        opening = {
            'id': 'opening', 'story_id': 'story', 'story': 'A briefing topic', 'speaker': 'tio',
            'speaker_id': 'host', 'guests': ['Expert One', 'Expert Two', 'Expert Three', 'Expert Four'],
            'brief': 'Welcome to the panel.', 'max_seconds': 8,
            'sources': [
                {'id': 'one', 'publisher': 'Sample Markets', 'language': 'en', 'url': 'https://example.org/article',
                 'quote': 'The full supporting passage from the first article.', 'claim': 'The first verified fact.'},
                {'id': 'two', 'publisher': 'Sample Economy', 'language': 'en', 'url': None,
                 'quote': 'An additional supporting passage.', 'claim': 'The first verified fact.'},
            ],
        }

        def turn(route):
            turns.append(route.request.post_data_json)
            if len(turns) == 1:
                opening_route.append(route)
            else:
                next_source = {**opening['sources'][0], 'claim': 'The next verified fact.'}
                current_story = {'story_id': 'story-2', 'story': 'Topic 2 headline'} if width == 524 and len(turns) >= 3 else {}
                route.fulfill(json={**opening, **current_story, 'id': 'next', 'speaker': 'Expert One', 'speaker_id': 'expert', 'sources': [next_source]})

        page.route('**/api/panel', start)
        page.route('**/api/panel/test/realtime/agents', lambda r: r.fulfill(json={
            'model': 'test', 'agents': [
                {'id': 'host', 'name': 'tio', 'role': 'host', 'voice': 'test'},
                {'id': 'expert', 'name': 'Expert One', 'role': 'guest', 'voice': 'test'},
            ],
        }))
        page.route('**/api/panel/test/realtime/next', turn)
        page.route('**/api/panel/test/holding', lambda r: r.fulfill(status=404))
        page.route('**/api/panel/test/live/audit', lambda r: r.fulfill(status=204))
        page.route('**/api/panel/test', lambda r: r.fulfill(status=204))
        page.route('https://api.openai.com/**', lambda r: r.abort())
        page.goto('http://localhost:5173/demo')
        expect(page.get_by_label('Listener', exact=True)).to_have_count(0)
        select_article = page.get_by_role('button', name='Select article: A briefing topic')
        if width == 320:
            card = page.locator('.selectable-stories article')
            rect = card.bounding_box()
            x, y = rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2
            touch = page.context.new_cdp_session(page)

            def swipe(dx):
                touch.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
                touch.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x + dx, 'y': y}]})
                # Hold still before lifting: an instant release starts a fling, and Chromium drops the next tap
                # as the one that stops it.
                page.wait_for_timeout(100)
                touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})

            swipe(-20)  # too short to act
            expect(select_article).to_have_attribute('aria-pressed', 'false')
            swipe(-60)  # a swipe left adds the story
            expect(select_article).to_have_attribute('aria-pressed', 'true')
            swipe(60)   # a swipe right takes it back out
            expect(select_article).to_have_attribute('aria-pressed', 'false')
            expect(card).not_to_have_attribute('data-swipe-armed', 'add')
            select_article.tap()
        else:
            select_article.click()
        expect(select_article).to_have_attribute('aria-pressed', 'true')
        select_article.press('Space')
        expect(select_article).to_have_attribute('aria-pressed', 'false')
        select_article.press('Enter')
        if width == 524:
            for i in (2, 3):
                page.get_by_role('button', name=f'Select article: Topic {i} headline').click()
        page.get_by_role('button', name='Start agent discussion').click()
        duration = page.get_by_role('slider', name='Duration')
        experts = page.get_by_role('slider', name='Expert participants')
        expect(duration).to_have_attribute('aria-valuenow', '4')
        page.evaluate("""async ({testMicrophone, blockSound}) => {
          const module = performance.getEntriesByType('resource')
              .find(entry => new URL(entry.name).pathname === '/src/realtime.ts');
          const { RealtimeAgent } = await import(module.name);
          let allowSound = !blockSound;
          document.addEventListener('click', event => {
            if (event.isTrusted && event.target.closest('[data-enable-sound="true"]')) allowSound = true;
          }, true);
          navigator.mediaDevices.getUserMedia = async () => {
            if (!testMicrophone) throw new DOMException('Mocked microphone denial', 'NotAllowedError');
            const context = new AudioContext();
            const input = context.createOscillator();
            const gain = context.createGain();
            const output = context.createMediaStreamDestination();
            gain.gain.value = 0;
            input.connect(gain).connect(output);
            input.start();
            await context.resume();
            const track = output.stream.getAudioTracks()[0];
            window.testMic = { gain, track, speaker: new MediaStream([track.clone()]), cancelled: 0 };
            window.testMic.track.enabled = false;
            return output.stream;
          };
          RealtimeAgent.connect = async (info, _model, _microphone, handlers) => {
            let finish;
            return {
              info, audio: {srcObject: window.testMic?.speaker, async play() {
                if (!allowSound) throw new DOMException('Mocked autoplay block', 'NotAllowedError');
              }}, speak: (_brief, _seconds, onText) => new Promise(resolve => {
                handlers.onSpeaking(true); finish = resolve;
                window.mockSpeech = { transcript: onText, drain() {
                  handlers.onSpeaking(false);
                  resolve({ text: 'The first verified fact.', seconds: 1, interrupted: false, audioDrained: true });
                }};
              }), context() {}, close() {},
              cancel() { window.testMic.cancelled++; handlers.onSpeaking(false); finish({ text: '', seconds: 1, interrupted: true, audioDrained: false }); },
              startListening() { window.testMic.track.enabled = true; },
              stopListening() {
                window.testMic.track.enabled = false;
                queueMicrotask(() => handlers.onListenerText('Next story please'));
              },
            };
          };
        }""", {'testMicrophone': width == 524, 'blockSound': width == 320})
        expect(experts).to_have_attribute('aria-valuenow', '2')
        controls = page.get_by_role('group', name='Briefing controls')
        expect(controls.get_by_role('button')).to_have_count(2)
        expect(controls.get_by_role('button', name='Play briefing')).to_be_disabled()
        expect(controls.get_by_role('button', name='Close briefing')).to_be_enabled()
        assert starts == [] and turns == []
        assert page.locator('dialog audio').get_attribute('src') is None
        expect(page.get_by_role('list', name='Topic checkpoints')).to_have_count(0)
        expect(page.locator('.session-headline')).to_have_count(0)
        duration.focus()
        duration.press('End')
        duration.press('ArrowRight')
        expect(duration).to_have_attribute('aria-valuenow', '15')
        duration.press('Home')
        duration.press('ArrowLeft')
        expect(duration).to_have_attribute('aria-valuenow', '1')
        x, y = point(duration, -170 + 160 * 6 / 14)
        page.mouse.click(x, y)
        expect(duration).to_have_attribute('aria-valuenow', '7')
        x, y = point(experts, 170, lower=True)
        end_x, end_y = point(experts, 10, lower=True)
        if width == 320:
            touch = page.context.new_cdp_session(page)
            touch.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
            touch.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': end_x, 'y': end_y}]})
            touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        else:
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(end_x, end_y, steps=12)
            page.mouse.up()
        expect(experts).to_have_attribute('aria-valuenow', '4')
        experts.press('Home')
        experts.press('ArrowUp')
        expect(experts).to_have_attribute('aria-valuenow', '3')
        experts.press('End')
        assert page.locator('.session-inner').evaluate('e => e.scrollWidth <= e.clientWidth')
        if width == 524:
            page.emulate_media(reduced_motion='no-preference')
        with page.expect_request('**/api/panel/test/realtime/next'):
            page.get_by_role('button', name='Begin briefing').click()
        orb = page.locator('.host-load')
        expect(orb).to_have_attribute('data-phase', 'loading')
        expect(orb.locator('.host-core')).to_have_css('opacity', '0')
        glass = orb.locator('.host-glass')
        expect(glass).to_have_css('--host-aperture', 'calc(100% - 7px)')
        expect(page.locator('.actor.host > span')).to_be_hidden()
        expect(page.locator('.player-status.sr-only')).to_have_count(1)
        ring = orb.locator('.host-ring')
        expect(ring).to_have_css('opacity', '1')
        expect(orb).to_have_css('transform', 'none')
        assert 'radial-gradient' in ring.evaluate('e => getComputedStyle(e).maskImage')
        expect(page.locator('.talk-deck')).to_have_count(0)
        if width == 524:
            assert ring.evaluate("e => getComputedStyle(e, '::before').animationPlayState") == 'running'
            page.locator('.agent-stage').evaluate("""stage => {
                window.guestArrivals = [];
                new MutationObserver(records => records.forEach(({target}) => {
                    const orb = stage.querySelector('.host-core');
                    const glass = stage.querySelector('.host-glass');
                    const motion = target.querySelector('.guest-bud').getAnimations()[0];
                    window.guestArrivals.push({opacity: getComputedStyle(orb).opacity,
                        aperture: getComputedStyle(glass).getPropertyValue('--host-aperture'),
                        from: motion?.effect.getKeyframes()[0].transform,
                        delay: motion?.effect.getTiming().delay});
                })).observe(stage, {subtree: true, attributeFilter: ['data-born']});
            }""")
        else:
            assert ring.evaluate("e => getComputedStyle(e, '::before').animationName") == 'none'
        opening_route.pop().fulfill(json=opening)
        if width == 320:
            enable_sound = controls.get_by_role('button', name='Enable sound')
            expect(enable_sound).to_be_visible()
            expect(page.get_by_role('list', name='Topic checkpoints')).to_have_count(0)
            assert page.evaluate('window.mockSpeech === undefined')
            enable_sound.tap()
            expect(enable_sound).to_have_count(0)
        expect(page.get_by_role('list', name='Topic checkpoints')).to_be_visible()
        expect(orb).to_have_attribute('data-phase', 'ready')
        if width == 524:
            expect(page.locator('.talk-deck')).to_have_css('animation-name', 'talk-deck-arrive')
        expect(glass).to_have_css('--host-aperture', '0%')
        expect(orb.locator('.host-core')).to_have_css('opacity', '1')
        expect(ring).to_have_css('opacity', '0')
        expect(ring).to_be_hidden()
        expect(orb).to_have_css('transform', 'none')
        assert len(starts) == len(turns) == 1
        assert starts[0]['minutes'] == 7 and starts[0]['expert_count'] == 4
        assert starts[0]['story_ids'] == (['story', 'story-2', 'story-3'] if width == 524 else ['story'])
        expect(page.locator('.checkpoint-title')).to_have_count(0)
        expect(page.locator('.session-headline')).to_have_text('A briefing topic')
        expect(page.get_by_role('group', name='Briefing setup')).to_have_count(0)
        expect(page.locator('.guest')).to_have_count(4)
        expect(page.locator('.actor.host > span')).to_have_text('Tio')
        expect(page.locator('.guest[data-born="true"]')).to_have_count(4)
        expect(page.get_by_role('textbox', name='Question for the panel')).to_have_count(0)
        expect(page.locator('.actor.host')).to_have_attribute('data-speaking', 'true')
        expect(page.locator('.guest[data-speaking="true"]')).to_have_count(0)
        if width == 524:
            arrivals = page.evaluate('window.guestArrivals')
            assert len(arrivals) == 4
            assert all(row['opacity'] == '1' and row['aperture'] == '0%' and row['from'].startswith('translate(') for row in arrivals)
            assert [row['delay'] for row in arrivals] == [0, 70, 140, 210]
            page.locator('.guest-bud').evaluate_all('nodes => Promise.all(nodes.flatMap(node => node.getAnimations().map(a => a.finished)))')
            expect(page.locator('.actor.host .orb-meter')).to_have_css('transform', 'matrix(1.12, 0, 0, 1.12, 0, 0)')
            host_orb = page.locator('.actor.host .orb-breath')
            expect(page.locator('.actor.host')).to_have_attribute('data-audio-pulse', 'true')
            expect(host_orb).to_have_css('transform', 'matrix(1, 0, 0, 1, 0, 0)')
            page.evaluate('window.testMic.gain.gain.value = 0.5')
            page.wait_for_function("new DOMMatrix(getComputedStyle(document.querySelector('.actor.host .orb-breath')).transform).d > 1.1")
            page.evaluate('window.testMic.gain.gain.value = 0')
        else:
            expect(page.locator('.actor.host .orb-meter')).to_have_css('transform', 'none')
        actors = [node.bounding_box() for node in page.locator('.actor .orb-skin').all()]
        for i, a in enumerate(actors):
            assert a['x'] >= 0 and a['x'] + a['width'] <= width
            for b in actors[i + 1:]:
                assert a['x'] + a['width'] <= b['x'] or b['x'] + b['width'] <= a['x'] or a['y'] + a['height'] <= b['y'] or b['y'] + b['height'] <= a['y']
        if width == 524:
            citations = page.get_by_role('region', name='Citations')
            facts = citations.get_by_role('list', name='Fact checks')
            outlets = citations.locator('.citation-outlets')
            expect(outlets.get_by_role('link', name='Sample Markets')).to_have_attribute('href', 'https://example.org/article')
            expect(outlets.get_by_text('Sample Economy', exact=True)).to_be_visible()
            claim = facts.get_by_role('link', name='The first verified fact.')
            expect(claim).to_be_visible()
            expect(facts.locator('li[data-current]').get_by_role('link', name='The first verified fact.')).to_be_visible()
            page.evaluate("window.mockSpeech.transcript('The first verified fact.')")
            page.evaluate('window.mockSpeech.drain()')
            expect(page.locator('.guest-0')).to_have_attribute('data-speaking', 'true')
            assert page.locator('.session-inner').evaluate('e => e.scrollHeight <= e.clientHeight')
            expect(claim).to_have_attribute('href', 'https://example.org/article')
            expect(claim).to_have_attribute('target', '_blank')
            deck = page.get_by_role('button', name='Interrupt with microphone')
            bars = deck.locator('.talk-waveform > span')
            bar = bars.first
            expect(deck).to_have_css('position', 'fixed')
            expect(bars).to_have_count(4)
            assert len(set(bars.evaluate_all('nodes => nodes.map(node => node.getBoundingClientRect().height)'))) == 1
            expect(bar).to_have_css('transform', 'none')
            deck.hover()
            expect(deck).to_have_css('transform', 'matrix(1.035, 0, 0, 1.035, 0, -2)')
            deck.click()
            expect(deck).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('.actor.host')).to_have_attribute('data-speaking', 'false')
            expect(page.locator('.guest-0')).to_have_attribute('data-speaking', 'false')
            assert page.evaluate('window.testMic.track.enabled && window.testMic.cancelled === 1')
            expect(bar).to_have_css('transform', 'matrix(1, 0, 0, 0.52, 0, 0)')
            page.evaluate('window.testMic.gain.gain.value = 0.5')
            page.wait_for_function("""() => {
                const [edge, center] = [...document.querySelectorAll('.talk-waveform > span')]
                    .map(bar => new DOMMatrix(getComputedStyle(bar).transform).d);
                return edge > 1 && center > edge + .2;
            }""")
            page.evaluate('window.testMic.gain.gain.value = 0')
            expect(bar).to_have_css('transform', 'matrix(1, 0, 0, 0.52, 0, 0)')
            page.emulate_media(reduced_motion='reduce')
            expect(bar).to_have_css('transform', 'none')
            with page.expect_request('**/api/panel/test/realtime/next') as question:
                deck.press('Space')
            assert question.value.post_data_json['question'] == 'Next story please'
            expect(page.locator('.guest-0')).to_have_attribute('data-speaking', 'true')
            expect(page.locator('.actor.host')).to_have_attribute('data-speaking', 'false')
            expect(page.locator('.session-headline')).to_have_text('Topic 2 headline')
            expect(page.locator('.agenda-label')).to_have_text('Topic 2 of 3')
            timeline = page.get_by_role('list', name='Topic checkpoints')
            assert timeline.evaluate("e => new DOMMatrix(getComputedStyle(e, '::after').transform).a") == .5
            assert not page.evaluate('window.testMic.track.enabled')
            expect(deck).to_have_attribute('aria-pressed', 'false')
            expect(deck).to_be_enabled()
            deck.click()
            expect(deck).to_have_attribute('aria-pressed', 'true')
        controls.get_by_role('button', name='Stop briefing').click()
        expect(page.get_by_role('button', name='Start agent discussion')).to_be_focused()
        if width == 524:
            page.wait_for_function("window.testMic.track.readyState === 'ended'")
        assert not errors, errors
        print(f'{width}px: silent setup, keyboard, arc drag, realtime request and four-expert layout passed', flush=True)
        page.close()

    for preview in (True, False):
        page = browser.new_page(viewport={'width': 524, 'height': 770}, reduced_motion='reduce')
        starts = []
        page.route('**/api/panel/stories', lambda r: r.fulfill(json={'sample': preview, 'stories': [
            {'id': 'story', 'title': 'A briefing topic', 'summary': 'Test summary', 'topic': 'Economy'}]}))

        def capture(route):
            starts.append(route.request.post_data_json)
            route.fulfill(status=422, json={'detail': [{'loc': ['body', 'briefing_plan'], 'msg': 'Test validation error'}]})

        page.route('**/api/panel', capture)
        page.goto('http://localhost:5173/demo')
        expect(page.locator('.story-grid article')).to_have_count(2 if preview else 1)
        page.get_by_role('button', name='Select article:', exact=False).first.click()
        page.get_by_role('button', name='Start agent discussion').click()
        expect(page.get_by_label('Playback')).to_have_count(0)
        expect(page.get_by_label('Format')).to_have_count(0)
        expect(page.get_by_text('Drag the arcs', exact=False)).to_have_count(0)
        slider = page.get_by_role('slider', name='Expert participants')
        expect(slider).to_have_attribute('aria-disabled', 'false')
        x, y = point(slider, 90, lower=True)
        page.mouse.click(x, y)
        expect(slider).to_have_attribute('aria-valuenow', '3')
        slider.press('End')
        expect(slider).to_have_attribute('aria-valuenow', '4')
        expect(slider).to_have_css('outline-style', 'none')
        expect(slider.locator('.dial-tick[data-selected="true"]')).to_have_css('stroke-width', '6px')
        expect(slider.locator('.dial-progress')).to_have_css('stroke-width', '4px')
        expect(slider.locator('.dial-tick[data-passed="true"]').first).to_have_css('stroke-width', '3.5px')
        expect(slider.locator('circle')).to_have_count(0)
        assert slider.locator('.dial-progress').get_attribute('d')
        page.get_by_role('button', name='Begin briefing').click()
        if preview:
            page.wait_for_function("document.querySelector('dialog audio')?.duration > 0")
            expect(page.get_by_role('button', name='Interrupt with microphone')).to_have_count(0)
            controls = page.get_by_role('group', name='Briefing controls')
            controls.get_by_role('button', name='Pause briefing').click()
            assert page.locator('dialog audio').evaluate('e => e.paused')
            controls.get_by_role('button', name='Play briefing').click()
            expect(controls.get_by_role('button', name='Pause briefing')).to_be_visible()
            assert starts == []
        else:
            expect(page.get_by_role('dialog').get_by_role('status')).to_have_text('Test validation error')
            assert starts[0]['mode'] == 'panel' and starts[0]['expert_count'] == 4
            page.keyboard.press('Escape')
        print(f'{"Recorded preview" if preview else "Generated panel"}: expert dial and simplified setup passed', flush=True)
        page.close()

    for cancel in (True, False):
        page = browser.new_page(viewport={'width': 524, 'height': 770}, reduced_motion='no-preference')
        starts = []
        page.route('**/api/panel/stories', lambda r: r.fulfill(json={'sample': False, 'stories': [
            {'id': 'story', 'title': 'A briefing topic', 'summary': 'Test summary', 'topic': 'Economy'}]}))

        def rejected(route):
            starts.append(route.request.post_data_json)
            route.fulfill(status=503, json={'detail': 'Test setup failure'})

        page.route('**/api/panel', rejected)
        page.goto('http://localhost:5173/demo')
        page.get_by_role('button', name='Select article:', exact=False).click()
        page.get_by_role('button', name='Start agent discussion').click()
        page.get_by_role('slider', name='Expert participants').press('End')
        page.get_by_role('button', name='Begin briefing').click()
        assert page.locator('.setup-dials').evaluate('e => e.getAnimations().length') == 1
        assert starts == []
        if cancel:
            page.keyboard.press('Escape')
            page.wait_for_timeout(600)
            assert starts == []
        else:
            page.get_by_role('button', name='Begin briefing').dispatch_event('click')
            expect(page.get_by_role('dialog').get_by_role('status')).to_have_text('Test setup failure')
            assert len(starts) == 1
            expect(page.get_by_role('slider', name='Expert participants')).to_have_attribute('aria-disabled', 'false')
            expect(page.locator('.setup-dials')).to_have_css('opacity', '1')
            page.get_by_role('slider', name='Expert participants').press('Home')
            with page.expect_response(lambda response: response.url.endswith('/api/panel') and response.request.method == 'POST'):
                page.get_by_role('button', name='Begin briefing').click()
            expect(page.get_by_role('dialog').get_by_role('status')).to_have_text('Test setup failure')
            assert len(starts) == 2 and starts[1]['expert_count'] == 2
        print(f'Normal motion: {"cancellation" if cancel else "single confirmation and adjustable retry"} passed', flush=True)
        page.close()
    browser.close()
