"""Run with Vite on localhost:5173 and Python Playwright + Edge installed.

All panel requests are mocked; this check makes no provider calls.
"""
import base64
import json
import math
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

AUDIO = base64.b64encode((Path(__file__).parents[1] / 'public/voices/rates-open.wav').read_bytes()).decode()


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
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/panel/stories', lambda r: r.fulfill(json={'sample': False, 'stories': [
            {'id': 'story', 'title': 'A briefing topic', 'summary': 'Test summary', 'topic': 'Economy'}]}))

        def start(route):
            starts.append(route.request.post_data_json)
            route.fulfill(json={'id': 'test', 'sample': False})

        def turn(route):
            turns.append(route.request.post_data_json)
            names = ['Expert One', 'Expert Two', 'Expert Three', 'Expert Four']
            route.fulfill(content_type='application/x-ndjson', body=json.dumps({'result': {
                'id': 'opening', 'story_id': 'story', 'story': 'A briefing topic', 'speaker': 'Host', 'host': 'Host',
                'guests': names, 'text': '. '.join(names) + '. Welcome to the panel.', 'sources': [], 'audio': AUDIO,
            }}) + '\n')

        page.route('**/api/panel', start)
        page.route('**/api/panel/test/events', turn)
        page.route('**/api/panel/test', lambda r: r.fulfill(status=204))
        page.route('**/api/recommend/catalog', lambda r: r.fulfill(json={'users': []}))
        page.goto('http://localhost:5173/demo')
        page.get_by_role('button', name='Include', exact=False).click()
        page.get_by_role('button', name='Start agent discussion').click()
        duration = page.get_by_role('slider', name='Duration')
        experts = page.get_by_role('slider', name='Expert participants')
        expect(duration).to_have_attribute('aria-valuenow', '4')
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
        page.get_by_role('button', name='Begin briefing').click()
        page.wait_for_function("document.querySelector('dialog audio')?.duration > 0")
        assert len(starts) == len(turns) == 1
        assert starts[0]['minutes'] == 7 and starts[0]['expert_count'] == 4
        assert starts[0]['story_ids'] == ['story']
        expect(page.get_by_role('list', name='Topic checkpoints')).to_be_visible()
        expect(page.locator('.checkpoint-title')).to_have_count(0)
        expect(page.locator('.session-headline')).to_have_text('A briefing topic')
        expect(page.get_by_role('group', name='Briefing setup')).to_have_count(0)
        page.evaluate("const audio = document.querySelector('dialog audio'); audio.currentTime = audio.duration * .8")
        expect(page.locator('.guest')).to_have_count(4)
        page.get_by_role('button', name='Pause briefing').click()
        expect(controls.get_by_role('button', name='Play briefing')).to_be_enabled()
        assert page.locator('dialog audio').evaluate('e => e.paused')
        expect(page.get_by_role('list', name='Topic checkpoints')).to_be_visible()
        controls.get_by_role('button', name='Play briefing').click()
        expect(controls.get_by_role('button', name='Pause briefing')).to_be_visible()
        controls.get_by_role('button', name='Pause briefing').click()
        actors = [node.bounding_box() for node in page.locator('.actor .orb-meter').all()]
        for i, a in enumerate(actors):
            assert a['x'] >= 0 and a['x'] + a['width'] <= width
            for b in actors[i + 1:]:
                assert a['x'] + a['width'] <= b['x'] or b['x'] + b['width'] <= a['x'] or a['y'] + a['height'] <= b['y'] or b['y'] + b['height'] <= a['y']
        controls.get_by_role('button', name='Stop briefing').click()
        expect(page.get_by_role('button', name='Start agent discussion')).to_be_focused()
        assert not errors, errors
        print(f'{width}px: silent setup, keyboard, arc drag, parameters, playback and four-expert layout passed', flush=True)
        page.close()

    for preview in (True, False):
        page = browser.new_page(viewport={'width': 524, 'height': 770}, reduced_motion='reduce')
        starts = []
        page.route('**/api/panel/stories', lambda r: r.fulfill(json={'sample': preview, 'stories': [
            {'id': 'story', 'title': 'A briefing topic', 'summary': 'Test summary', 'topic': 'Economy'}]}))

        def capture(route):
            starts.append(route.request.post_data_json)
            route.fulfill(status=503, json={'detail': 'Test endpoint reached'})

        page.route('**/api/panel', capture)
        page.route('**/api/recommend/catalog', lambda r: r.fulfill(json={'users': []}))
        page.goto('http://localhost:5173/demo')
        expect(page.locator('.story-grid article')).to_have_count(2 if preview else 1)
        if not preview:
            page.get_by_role('button', name='Profile', exact=True).click()
            page.get_by_label('Format').select_option('presenter')
            page.get_by_role('button', name='Save preferences').click()
            page.get_by_role('button', name='News feed', exact=True).click()
        page.get_by_role('button', name='Include', exact=False).first.click()
        page.get_by_role('button', name='Start agent discussion').click()
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
            assert starts == []
        else:
            expect(page.get_by_role('dialog').get_by_role('status')).to_have_text('Test endpoint reached')
            assert starts[0]['mode'] == 'panel' and starts[0]['expert_count'] == 4
            page.keyboard.press('Escape')
            page.get_by_role('button', name='Profile', exact=True).click()
            expect(page.get_by_label('Format')).to_have_value('presenter')
        print(f'{"Recorded preview" if preview else "Presenter session"}: expert dial, thick progress and leading tick, focus cue and format handling passed', flush=True)
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
        page.route('**/api/recommend/catalog', lambda r: r.fulfill(json={'users': []}))
        page.goto('http://localhost:5173/demo')
        page.get_by_role('button', name='Include', exact=False).click()
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
