"""Run with Vite on localhost:5173, Python Playwright and Edge. APIs are mocked."""
from playwright.sync_api import sync_playwright, expect

ADA = '11111111-1111-4111-8111-111111111111'
GRACE = '22222222-2222-4222-8222-222222222222'
VOICES = [
    {'id': ADA, 'name': 'Ada', 'tagline': 'Technology and markets', 'can_preview': True},
    {'id': GRACE, 'name': 'Grace', 'tagline': 'A clear economic perspective', 'can_preview': True},
    {'id': '33333333-3333-4333-8333-333333333333', 'name': 'Sam', 'tagline': 'World news', 'can_preview': False},
]


def open_voices(page):
    page.goto('http://localhost:5173/')
    page.get_by_role('button', name='Create an account', exact=True).click()
    expect(page.locator('#login-title .brand')).to_have_css('font-size', '43px')
    for label, value in [('Display name', 'Test listener'), ('First name', 'Test'), ('Last name', 'Listener')]:
        page.get_by_label(label, exact=True).fill(value)
    page.get_by_role('button', name='Continue', exact=True).click()
    expect(page.get_by_label('What do you want from your briefing?', exact=True)).to_have_count(0)
    expect(page.get_by_label('Preferred listening time', exact=True)).to_have_count(0)
    page.get_by_label('Technology', exact=True).check()
    page.get_by_role('button', name='Continue', exact=True).click()
    expect(page.get_by_role('heading', name='Pick your favorite voices')).to_be_visible()
    expect(page.locator('#login-title .brand')).to_have_css('font-size', '43px')


with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge')
    for width, theme in [(320, 'light'), (524, 'dark'), (1440, 'light')]:
        page = browser.new_page(viewport={'width': width, 'height': 900}, reduced_motion='reduce', has_touch=width == 320)
        errors, previews, signups = [], [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('tio-theme','{theme}')")
        page.add_init_script('Math.random = () => .999')
        page.route('**/api/voice/catalog', lambda r: r.fulfill(json=VOICES))

        def preview(route):
            previews.append(route.request.url)
            route.fulfill(json={'url': '/voices/rates-open.wav'})

        def signup(route):
            signups.append(route.request.post_data_json)
            route.fulfill(json={'user': {'id': 'test'}})

        page.route('**/api/voice/catalog/*/preview', preview)
        page.route('**/auth/v1/signup', signup)
        open_voices(page)
        expect(page.get_by_role('group', name='Voice preview', exact=True)).to_have_css('border-top-width', '0px')
        expect(page.get_by_label('Search voices')).to_have_count(0)
        for selector in ['.voice-picker-heading h3', '.voice-carousel-info h4']:
            expect(page.locator(selector)).to_have_css('font-family', 'Questrial, sans-serif')
        dots = page.get_by_role('group', name='Nearby voices')
        first = page.locator('.voice-carousel-info h4').inner_text()
        page.get_by_role('button', name='Next voice', exact=True).click()
        expect(page.locator('.voice-carousel-info h4')).not_to_have_text(first)
        expect(page.locator('.voice-preview-status')).to_contain_text('Previewing')
        page.get_by_role('button', name='Previous voice', exact=True).click()
        expect(page.locator('.voice-carousel-info h4')).to_have_text(first)
        expect(page.locator('.voice-preview-status')).to_contain_text('Previewing')
        orb = page.locator('.voice-save-orb')
        orb.scroll_into_view_if_needed()
        box = orb.bounding_box()
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        if width == 320:
            touch = page.context.new_cdp_session(page)
            touch.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
            touch.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x - 65, 'y': y}]})
            touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        else:
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x - 65, y, steps=10)
            page.mouse.up()
        expect(page.locator('.voice-carousel-info h4')).to_have_text('Grace')
        expect(page.locator('.voice-preview-status')).to_contain_text('Previewing')
        expect(page.locator('.saved-voices li')).to_have_count(0)
        page.get_by_role('button', name='Mute voice previews').click()
        assert page.locator('.voice-auditions audio').evaluate('e => e.muted')
        page.get_by_role('button', name='Unmute voice previews').click()
        dots.get_by_role('button', name='Ada', exact=True).click()
        expect(page.locator('.voice-preview-status')).to_contain_text('Previewing')
        page.get_by_role('button', name='Save Ada', exact=True).click()
        dots.get_by_role('button', name='Grace', exact=True).click()
        expect(page.locator('.voice-preview-status')).to_contain_text('Previewing')
        page.get_by_role('button', name='Save Grace', exact=True).click()
        expect(page.locator('.saved-voices li')).to_have_count(2)
        page.locator('.saved-voices').get_by_role('button', name='Remove Grace from saved voices').click()
        expect(page.locator('.saved-voices li')).to_have_count(1)
        page.get_by_role('button', name='Save Grace', exact=True).click()
        dots.get_by_role('button', name='Sam', exact=True).click()
        expect(page.locator('.voice-preview-status')).to_contain_text('No preview available')
        assert page.locator('.voice-auditions audio').evaluate('e => e.paused')
        dots.get_by_role('button', name='Ada', exact=True).click()
        expect(page.locator('.voice-save-orb')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('.voice-carousel-actions')).to_have_count(0)
        page.get_by_role('button', name='Back', exact=True).click()
        page.get_by_role('button', name='Continue', exact=True).click()
        dots.get_by_role('button', name='Ada', exact=True).click()
        expect(page.locator('.voice-save-orb')).to_have_attribute('aria-pressed', 'true')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width == 524:
            page.screenshot(path='frontend/node_modules/signup-voices.png', full_page=True)
        page.get_by_role('button', name='Continue', exact=True).click()
        expect(page.get_by_text('Step 4 of 4')).to_be_visible()
        expect(page.locator('#login-title .brand')).to_have_css('font-size', '43px')
        page.get_by_label('Email', exact=True).fill('voice-test@example.invalid')
        page.get_by_label('Password', exact=True).fill('Example-password-123!')
        page.get_by_label('Confirm password', exact=True).fill('Example-password-123!')
        page.get_by_role('checkbox').last.check()
        page.get_by_role('button', name='Create account', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('check your inbox')
        assert signups[0]['data']['tio_onboarding']['preferred_persona_ids'] == [ADA, GRACE]
        assert signups[0]['data']['tio_onboarding']['listener_goal'] == 'Technology'
        assert 'preferred_listen_time' not in signups[0]['data']['tio_onboarding']
        assert any(ADA in url for url in previews) and any(GRACE in url for url in previews) and not errors, (previews, errors)
        print(f'{width}px {theme}: streamlined picker, playback, favorites, back navigation and signup metadata passed', flush=True)
        page.close()

    page = browser.new_page(reduced_motion='reduce')
    page.route('**/api/voice/catalog', lambda r: r.fulfill(status=503, json={'detail': 'Unavailable'}))
    open_voices(page)
    expect(page.get_by_role('status')).to_contain_text('continue without choosing')
    page.get_by_role('button', name='Continue', exact=True).click()
    expect(page.get_by_label('Email', exact=True)).to_be_visible()
    print('Unavailable catalog: clear status and optional voice selection passed')
    browser.close()
