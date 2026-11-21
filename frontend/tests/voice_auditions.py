"""Run with Vite on localhost:5173, FastAPI, Python Playwright and Chrome."""
from pathlib import Path
from tempfile import gettempdir

from playwright.sync_api import sync_playwright, expect

ADA = '11111111-1111-4111-8111-111111111111'
GRACE = '22222222-2222-4222-8222-222222222222'
VOICES = [
    {'id': ADA, 'name': 'Ada', 'tagline': 'Technology and markets', 'preview_url': '/api/voice/previews/kingston-public-radio.wav'},
    {'id': GRACE, 'name': 'Grace', 'tagline': 'A clear economic perspective', 'preview_url': '/api/voice/previews/after-dark-desk.wav'},
    {'id': '33333333-3333-4333-8333-333333333333', 'name': 'Sam', 'tagline': 'World news', 'preview_url': None},
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
    expect(page.get_by_role('heading', name='Pick up to three favorite voices')).to_be_visible()
    expect(page.locator('#login-title .brand')).to_have_css('font-size', '43px')


with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome')
    for width, theme in [(320, 'light'), (524, 'dark'), (1440, 'light')]:
        page = browser.new_page(viewport={'width': width, 'height': 900}, reduced_motion='reduce', has_touch=width == 320)
        errors, requests, signups = [], [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda request: requests.append(request.url))
        page.add_init_script(f"localStorage.setItem('tio-theme','{theme}')")
        page.add_init_script('Math.random = () => .999')
        page.route('**/api/voice/catalog', lambda r: r.fulfill(json=VOICES))

        def signup(route):
            signups.append(route.request.post_data_json)
            route.fulfill(json={'user': {'id': 'test'}})

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
        previous_audio = page.locator('.voice-auditions audio').element_handle()
        page.get_by_role('button', name='Previous voice', exact=True).click()
        expect(page.locator('.voice-carousel-info h4')).to_have_text(first)
        expect(page.locator('.voice-preview-status')).to_contain_text('Previewing')
        assert previous_audio.evaluate('e => e.paused')
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
        expect(page.get_by_role('button', name='Play sample')).to_have_count(0)
        dots.get_by_role('button', name='Ada', exact=True).click()
        expect(page.locator('.voice-save-orb')).to_have_attribute('aria-pressed', 'true')
        expect(page.get_by_role('button', name='Play sample')).to_be_visible()
        page.get_by_role('button', name='Back', exact=True).click()
        page.get_by_role('button', name='Continue', exact=True).click()
        dots.get_by_role('button', name='Ada', exact=True).click()
        expect(page.locator('.voice-save-orb')).to_have_attribute('aria-pressed', 'true')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=Path(gettempdir()) / f'tio-signup-voices-{width}.png', full_page=True)
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
        assert all(any(url.endswith(voice['preview_url']) for url in requests) for voice in VOICES[:2])
        assert not any('/api/voice/catalog/' in url for url in requests) and not errors, errors
        print(f'{width}px {theme}: streamlined picker, playback, favorites, back navigation and signup metadata passed', flush=True)
        page.close()

    page = browser.new_page(reduced_motion='reduce')
    page.add_init_script("""
        Math.random = () => .999;
        const play = HTMLMediaElement.prototype.play;
        let clicked = false;
        document.addEventListener('click', event => {
            if (event.target.closest('.voice-preview-play')) clicked = true;
        }, true);
        HTMLMediaElement.prototype.play = function () {
            if (!clicked) return Promise.reject(new DOMException('Autoplay blocked', 'NotAllowedError'));
            return play.call(this);
        };
    """)
    page.route('**/api/voice/catalog', lambda route: route.fulfill(json=VOICES))
    open_voices(page)
    audio = page.locator('.voice-auditions audio')
    assert audio.evaluate('e => e.paused')
    page.get_by_role('button', name='Play sample').click()
    expect(page.locator('.voice-preview-status')).to_contain_text('Previewing')
    page.wait_for_function("document.querySelector('.voice-auditions audio').currentTime > 0")
    print('Blocked autoplay: Play sample starts the WAV', flush=True)
    page.close()

    page = browser.new_page(reduced_motion='reduce')
    page.route('**/api/voice/catalog', lambda r: r.fulfill(status=503, json={'detail': 'Unavailable'}))
    open_voices(page)
    expect(page.get_by_role('status')).to_contain_text('continue without choosing')
    page.get_by_role('button', name='Continue', exact=True).click()
    expect(page.get_by_label('Email', exact=True)).to_be_visible()
    print('Unavailable catalog: clear status and optional voice selection passed')
    page.close()

    page = browser.new_page(viewport={'width': 537, 'height': 770}, reduced_motion='reduce')
    page.route('**/api/voice/catalog', lambda route: route.fulfill(json=VOICES))
    page.route('**/api/profile/embed', lambda route: route.fulfill(status=204))
    page.route('**/api/recommend', lambda route: route.fulfill(json={'article_ids': ['story']}))
    page.route('**/api/recommend/lookup', lambda route: route.fulfill(json={
        'story': {'headline': 'A briefing topic', 'publication': 'Sample Markets', 'language': 'en'},
    }))
    page.route('**/api/briefing/generate', lambda route: route.fulfill(json={
        'segments': [{'article_ids': ['story']}], 'hosts': [ADA, GRACE], 'tones': {},
    }))

    def sign_in(route):
        email = route.request.post_data_json['email']
        route.fulfill(json={'access_token': 'test-session', 'user': {'id': email, 'user_metadata': {
            'tio_onboarding': {'interests_text': 'Technology, Urban planning', 'preferred_persona_ids': [ADA]}
        }}})

    page.route('**/auth/v1/token?grant_type=password', sign_in)

    def open_profile(email):
        page.goto('http://localhost:5173/')
        page.get_by_label('Email', exact=True).fill(email)
        page.get_by_label('Password', exact=True).fill('Example-password-123!')
        page.get_by_role('button', name='Sign in', exact=True).click()
        page.get_by_role('button', name='Profile', exact=True).click()
        expect(page.get_by_role('heading', name='Profile', exact=True)).to_be_visible()

    first_email = 'profile-one@example.invalid'
    open_profile(first_email)
    expect(page.locator('.workspace .masthead')).to_have_count(0)
    expect(page.get_by_text('On this device', exact=True)).to_have_count(0)
    expect(page.get_by_label('Listener profile')).to_have_count(0)
    expect(page.get_by_label('Playback')).to_have_count(0)
    appearance = page.get_by_role('region', name='Appearance')
    assert appearance.bounding_box()['y'] < page.locator('.interest-options').bounding_box()['y']
    expect(page.get_by_label('Technology', exact=True)).to_be_checked()
    expect(page.get_by_label('Other interests (optional)')).to_have_value('Urban planning')
    saved = page.locator('.saved-voices')
    expect(saved.get_by_text('Ada', exact=True)).to_be_visible()
    page.get_by_role('group', name='Nearby voices').get_by_role('button', name='Ada', exact=True).click()
    page.get_by_role('button', name='Play sample').click()
    expect(page.locator('.voice-preview-status')).to_contain_text('Previewing')
    page.wait_for_function("document.querySelector('.voice-auditions audio').currentTime > 0")
    page.get_by_label('Technology', exact=True).uncheck()
    page.get_by_label('Company earnings', exact=True).check()
    page.get_by_label('Other interests (optional)').fill('City budgets')
    saved.get_by_role('button', name='Remove Ada from saved voices').click()
    page.get_by_role('button', name='Briefings', exact=True).click()
    expect(page.get_by_role('heading', name='Briefings', exact=True)).to_be_visible()
    expect(page.locator('.workspace .masthead')).to_have_count(0)
    page.get_by_role('button', name='News feed', exact=True).click()
    expect(page.locator('.workspace .masthead')).to_be_visible()
    open_profile(first_email)
    expect(page.get_by_label('Technology', exact=True)).not_to_be_checked()
    expect(page.get_by_label('Company earnings', exact=True)).to_be_checked()
    expect(page.get_by_label('Other interests (optional)')).to_have_value('City budgets')
    expect(saved.get_by_text('Ada', exact=True)).to_have_count(0)
    page.get_by_role('button', name='News feed', exact=True).click()
    page.get_by_role('button', name='Select article:', exact=False).first.click()
    page.get_by_role('button', name='Start agent discussion').click()
    page.route('**/api/panel', lambda route: route.fulfill(status=503, json={'detail': 'Test endpoint reached'}))
    with page.expect_request('**/api/panel') as start:
        page.get_by_role('button', name='Begin briefing').click()
    assert 'Company earnings, City budgets' in start.value.post_data_json['profile']
    open_profile('profile-two@example.invalid')
    expect(page.get_by_label('Technology', exact=True)).to_be_checked()
    expect(page.get_by_label('Company earnings', exact=True)).not_to_be_checked()
    expect(saved.get_by_text('Ada', exact=True)).to_be_visible()
    page.goto('http://localhost:5173/demo')
    page.get_by_role('button', name='Profile', exact=True).click()
    expect(page.get_by_label('Technology', exact=True)).not_to_be_checked()
    expect(saved.get_by_text('Ada', exact=True)).to_have_count(0)
    print('Profile: layout, signup defaults, autosave, and account/demo isolation passed')
    browser.close()
