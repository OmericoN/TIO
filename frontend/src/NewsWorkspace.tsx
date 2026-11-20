import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { AudioRibbon, Brand, Icon } from './Brand'
import { Briefings } from './BriefingHistory'
import { Orb } from './Orb'
import { loadSettings, Profile } from './Profile'
import { sampleHeadlines } from './briefings'
import type { Headline, Preferences } from './briefings'

const loadPlayer = () => import('./PanelPlayer').then(module => ({ default: module.PanelPlayer }))
const PanelPlayer = lazy(loadPlayer)
const defaultProfile = 'Explain what changed and why it matters to a general news listener.'

export function NewsWorkspace({ owner }: { owner: string }) {
  const [view, setView] = useState<'feed' | 'briefings' | 'profile'>('feed')
  const [stories, setStories] = useState<Headline[]>([])
  const [sample, setSample] = useState(true)
  const [historyVersion, setHistoryVersion] = useState(0)
  const [online, setOnline] = useState(false)
  const [savedSettings] = useState(() => loadSettings(owner))
  const [recorded, setRecorded] = useState(savedSettings?.recorded ?? true)
  const [preferences, setPreferences] = useState<Preferences>(savedSettings?.preferences ?? { profile: defaultProfile, mode: 'panel', minutes: 4 })
  const [picks, setPicks] = useState<Record<string, boolean>>({})
  const [notice, setNotice] = useState('')
  const [question, setQuestion] = useState('')
  const [submittedQuestion, setSubmittedQuestion] = useState('')
  const [launch, setLaunch] = useState<{ x: number; y: number; context: AudioContext | null } | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const drag = useRef<{ id: number; x: number; y: number } | null>(null)
  const selected = Object.keys(picks).filter(id => picks[id]).map(id => stories.find(s => s.id === id)!).filter(Boolean)

  useEffect(() => {
    let mounted = true
    async function load() {
      try {
        const response = await fetch('/api/panel/stories', { signal: AbortSignal.timeout(5000) })
        if (!response.ok) throw new Error()
        const catalog = await response.json()
        if (!Array.isArray(catalog.stories)) throw new Error()
        if (!mounted) return
        setStories(catalog.sample ? sampleHeadlines : catalog.stories)
        setSample(catalog.sample)
        setRecorded(catalog.sample && (savedSettings?.recorded ?? true))
        setOnline(true)
      } catch { if (mounted) { setStories(sampleHeadlines); setRecorded(true) } }
    }
    void load()
    return () => { mounted = false }
  }, [])

  function choose(story: Headline, include: boolean) {
    if (include && !picks[story.id] && selected.length >= 6) {
      setNotice('Your lineup holds six headlines. Remove one before adding another.')
      return
    }
    setPicks(previous => ({ ...previous, [story.id]: include }))
    setNotice((include ? 'Included ' : 'Skipped ') + story.title)
  }

  function start() {
    if (!selected.length || (!recorded && (!preferences.profile.trim() || !Number.isInteger(preferences.minutes) || preferences.minutes < 1 || preferences.minutes > 15))) {
      setView(selected.length ? 'profile' : 'feed')
      setNotice(selected.length ? 'Check the listener profile and briefing length.' : 'Choose at least one headline to start your briefing.')
      requestAnimationFrame(() => (selected.length ? document.querySelector<HTMLTextAreaElement>('.profile-settings textarea')?.focus() : document.getElementById('stories-title')?.scrollIntoView({ block: 'center' })))
      return
    }
    const rect = trigger.current!.getBoundingClientRect()
    let context = null
    try { context = new AudioContext(); void context.resume().catch(() => {}) } catch { /* Native playback still works. */ }
    setLaunch({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, context })
  }

  return <div className="workspace">
    <a className="skip-link" href="#workspace-content">Skip to content</a>
    <header className={view === 'feed' ? 'masthead with-search' : 'masthead'}>
      {view === 'feed' && <AudioRibbon />}
      <div className="masthead-inner"><div className="brand-lockup"><Brand /><span className="publication">The Liquid Briefing</span></div></div>
    </header>
    {view === 'feed' && <div className="search-rail">
      <form className="article-search" role="search" aria-label="Article search preview" onSubmit={event => {
        event.preventDefault()
        if (!question.trim()) return
        setSubmittedQuestion(question.trim())
        setQuestion('')
        window.scrollTo(0, 0)
      }}>
        <Icon name="search" />
        <label htmlFor="article-question" className="sr-only">Ask about the articles</label>
        <input id="article-question" type="search" enterKeyHint="search" placeholder="Search the news…" value={question} maxLength={500} onChange={event => setQuestion(event.target.value)} />
        <button type="submit" aria-label="Ask" disabled={!question.trim()}><Icon name="arrow" /></button>
      </form>
    </div>}
    <div className="workspace-dock">
      <button ref={trigger} type="button" className="agent-launch" aria-label="Start agent discussion" title="Start agent discussion" onFocus={() => void loadPlayer()} onPointerEnter={() => void loadPlayer()} onClick={start}>
        <Orb />{selected.length > 0 && <small aria-hidden="true">{selected.length}</small>}
      </button>
      <nav aria-label="Workspace" className="workspace-nav">
        {(['feed', 'briefings', 'profile'] as const).map(id => <button type="button" key={id} aria-current={view === id ? 'page' : undefined} onClick={() => { setView(id); window.scrollTo(0, 0) }}>
          <Icon name={id === 'feed' ? 'feed' : id === 'profile' ? 'profile' : 'discussion'} />{id === 'feed' ? 'News feed' : id === 'profile' ? 'Profile' : 'Briefings'}
        </button>)}
      </nav>
    </div>
    <section id="workspace-content" tabIndex={-1} aria-label="News workspace" className="page-width">
      {view === 'profile' ? <Profile preferences={preferences} recorded={recorded} sample={sample} online={online} onSave={settings => {
        setPreferences(settings.preferences)
        setRecorded(settings.recorded)
        try {
          localStorage.setItem('tio-profile-' + owner, JSON.stringify(settings))
          return 'Preferences saved on this device.'
        } catch { return 'Applied for this visit. Your browser could not save these preferences.' }
      }} /> : view === 'briefings' ? <Briefings key={historyVersion} owner={owner} /> : <>
        {submittedQuestion && <div role="status" className="search-result"><strong>{submittedQuestion}</strong><p>Article search is a preview. It hasn’t searched the dataset.</p></div>}
        <h1 className="sr-only">News feed</h1>
        <span role="status" className={notice.startsWith('Your lineup') || notice.startsWith('Choose') || notice.startsWith('Check') ? 'selection-notice' : 'sr-only'}>{notice}</span>
        <section aria-labelledby="stories-title">
          <div className="section-title"><h2 id="stories-title">Top articles</h2></div>
          <ul className="story-grid selectable-stories">{stories.map(story => <li key={story.id}>
            <article data-choice={picks[story.id] === undefined ? undefined : picks[story.id] ? 'include' : 'skip'}
              onPointerDown={e => {
                if (!e.isPrimary || e.button !== 0 || (e.target as HTMLElement).closest('button')) return
                drag.current = { id: e.pointerId, x: e.clientX, y: e.clientY }
                e.currentTarget.setPointerCapture(e.pointerId)
              }} onPointerMove={e => {
                const from = drag.current
                if (!from || from.id !== e.pointerId) return
                const dx = e.clientX - from.x
                if (Math.abs(dx) < 12 || Math.abs(e.clientY - from.y) > Math.abs(dx)) return
                e.currentTarget.dataset.swipe = dx > 0 ? 'include' : 'skip'
                if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
                  const offset = Math.max(-32, Math.min(32, dx / 3))
                  e.currentTarget.style.transform = `translateX(${offset}px) rotate(${offset / 24}deg)`
                }
              }} onLostPointerCapture={e => {
                drag.current = null
                delete e.currentTarget.dataset.swipe
                e.currentTarget.style.removeProperty('transform')
              }} onPointerUp={e => {
                const start = drag.current
                if (!start || start.id !== e.pointerId) return
                drag.current = null
                const dx = e.clientX - start.x
                if (Math.abs(dx) >= 48 && Math.abs(dx) > 1.5 * Math.abs(e.clientY - start.y)) choose(story, dx > 0)
              }}>
              {typeof story.thumb_url === 'string' && /^https?:\/\//i.test(story.thumb_url) &&
                <img className="story-photo" src={story.thumb_url} alt="" draggable={false} loading="lazy"
                  referrerPolicy="no-referrer" onError={event => { event.currentTarget.hidden = true }} />}
              <p className="topic">{story.topic}</p><h3>{story.title}</h3><p className="story-summary">{story.summary}</p>
              <div className="story-vote"><button aria-pressed={picks[story.id] === false} onClick={() => choose(story, false)}>← {picks[story.id] === false ? 'Skipped' : 'Skip'}</button>
              <button aria-pressed={picks[story.id] === true} onClick={() => choose(story, true)}>{picks[story.id] ? 'Included' : 'Include'} →</button></div>
            </article></li>)}</ul>
          {!stories.length && <p role="status">{online ? 'No indexed headlines are available.' : 'Loading headlines…'}</p>}
        </section>
      </>}
    </section>
    {launch && <Suspense fallback={<p className="session-loading" role="status">Opening briefing…</p>}><PanelPlayer owner={owner} stories={selected} preferences={preferences} recorded={recorded} origin={launch} onClose={() => {
      if (launch.context && launch.context.state !== 'closed') void launch.context.close().catch(() => {})
      setHistoryVersion(v => v + 1)
      setLaunch(null)
      requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }))
    }} /></Suspense>}
  </div>
}
