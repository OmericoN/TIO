import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { AudioRibbon, Brand, Icon } from './Brand'
import { Briefings } from './BriefingHistory'
import { Orb } from './Orb'
import { sampleHeadlines } from './briefings'
import type { DiscussionPlan, Headline, Preferences, PreparedBriefing } from './briefings'

const loadPlayer = () => import('./PanelPlayer').then(module => ({ default: module.PanelPlayer }))
const PanelPlayer = lazy(loadPlayer)
const defaultProfile = 'Explain what changed and why it matters to a general news listener.'

type Listener = { id: string; name: string; minutes: number | null }
type ArticleLookup = Record<string, { headline: string; publication: string; language: string }>

async function requestJson<T>(url: string, signal?: AbortSignal, body?: object): Promise<T> {
  const response = await fetch(url, body ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  } : { signal })
  const result = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(typeof result?.detail === 'string' ? result.detail : result?.detail?.[0]?.msg || 'The backend is unavailable.')
  }
  return result
}

async function lookupHeadlines(ids: string[], signal?: AbortSignal): Promise<Headline[]> {
  if (!ids.length) return []
  const articles = await requestJson<ArticleLookup>('/api/recommend/lookup', signal, { article_ids: ids })
  return ids.flatMap(id => {
    const article = articles[id]
    return article ? [{ id, title: article.headline, topic: article.publication, summary: article.language.toUpperCase() }] : []
  })
}

export function NewsWorkspace({ owner, listenerId }: { owner: string; listenerId?: string }) {
  const demo = owner === 'demo'
  const [view, setView] = useState<'feed' | 'briefings'>('feed')
  const [stories, setStories] = useState<Headline[]>([])
  const [demoListeners, setDemoListeners] = useState<Listener[] | null>(null)
  const [demoListener, setDemoListener] = useState('')
  const [feedError, setFeedError] = useState('')
  const [preparing, setPreparing] = useState(false)
  const [sample, setSample] = useState(true)
  const [historyVersion, setHistoryVersion] = useState(0)
  const [online, setOnline] = useState(false)
  const [recorded, setRecorded] = useState(true)
  const [preferences, setPreferences] = useState<Preferences>({ profile: '', mode: 'panel', minutes: 4 })
  const [picks, setPicks] = useState<Record<string, boolean>>({})
  const [notice, setNotice] = useState('')
  const [question, setQuestion] = useState('')
  const [submittedQuestion, setSubmittedQuestion] = useState('')
  const [launch, setLaunch] = useState<{ x: number; y: number; context: AudioContext | null; prepared?: PreparedBriefing } | null>(null)
  const [briefingError, setBriefingError] = useState('')
  const trigger = useRef<HTMLButtonElement>(null)
  const drag = useRef<{ id: number; x: number; y: number } | null>(null)
  const userId = listenerId || demoListener
  const waitingForCatalog = demo && demoListeners === null
  const selected = Object.keys(picks).filter(id => picks[id]).map(id => stories.find(s => s.id === id)!).filter(Boolean)

  useEffect(() => {
    if (!demo) return
    const controller = new AbortController()
    void requestJson<{ users: Listener[] }>('/api/recommend/catalog',
      AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]))
      .then(({ users }) => {
        setDemoListeners(users)
        setDemoListener(current => users.some(user => user.id === current) ? current : users[0]?.id ?? '')
      })
      .catch(() => { if (!controller.signal.aborted) setDemoListeners([]) })
    return () => controller.abort()
  }, [demo])

  useEffect(() => {
    if (waitingForCatalog) return
    const controller = new AbortController()
    setStories([])
    setPicks({})
    setOnline(false)
    setFeedError('')
    async function load() {
      try {
        if (userId) {
          const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(90_000)])
          const recommendations = await requestJson<{ article_ids: string[] }>('/api/recommend', signal, { user_id: userId })
          const headlines = await lookupHeadlines(recommendations.article_ids, signal)
          if (controller.signal.aborted) return
          setStories(headlines)
          setSample(false)
          setRecorded(false)
        } else {
          const catalog = await requestJson<{ sample: boolean; stories: Headline[] }>('/api/panel/stories',
            AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]))
          if (controller.signal.aborted) return
          setStories(catalog.sample ? sampleHeadlines : catalog.stories)
          setSample(catalog.sample)
          setRecorded(catalog.sample)
        }
        setOnline(true)
      } catch (error) {
        if (controller.signal.aborted) return
        if (userId) {
          setFeedError(error instanceof Error ? error.message : 'Recommendations could not be loaded.')
        } else {
          setStories(sampleHeadlines)
          setSample(true)
          setRecorded(true)
        }
      }
    }
    void load()
    return () => controller.abort()
  }, [userId, waitingForCatalog])

  useEffect(() => {
    if (!demo || !new URLSearchParams(location.search).has('briefing')) return
    window.history.replaceState(null, '', location.pathname)
    try {
      const stored = sessionStorage.getItem('tio-briefing-plan')
      sessionStorage.removeItem('tio-briefing-plan')
      const prepared = stored ? JSON.parse(stored) as PreparedBriefing : null
      if (!prepared?.stories.length) throw new Error()
      if (prepared.listenerId) setDemoListener(prepared.listenerId)
      setLaunch({ x: window.innerWidth / 2, y: window.innerHeight / 2, context: null, prepared })
    } catch {
      setBriefingError('The prepared briefing is unavailable. Generate it again in the recommender lab.')
    }
  }, [demo])

  function choose(story: Headline, include: boolean) {
    if (include && !picks[story.id] && selected.length >= 6) {
      setNotice('Your lineup holds six headlines. Remove one before adding another.')
      return
    }
    setPicks(previous => ({ ...previous, [story.id]: include }))
    setNotice((include ? 'Included ' : 'Skipped ') + story.title)
  }

  async function start() {
    if (preparing) return
    if (!selected.length || (!recorded && (!Number.isInteger(preferences.minutes) || preferences.minutes < 1 || preferences.minutes > 15))) {
      setView('feed')
      setNotice(selected.length ? 'Check the briefing length.' : 'Choose at least one headline to start your briefing.')
      requestAnimationFrame(() => document.getElementById(selected.length ? 'preferences' : 'stories-title')?.scrollIntoView({ block: 'center' }))
      return
    }
    const rect = trigger.current!.getBoundingClientRect()
    const origin = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, context: null }
    setBriefingError('')
    if (userId) {
      setPreparing(true)
      setView('feed')
      try {
        const { segments, hosts, tones, profile } = await requestJson<DiscussionPlan & { profile: string }>('/api/briefing/generate',
          undefined, {
            user_id: userId, article_ids: stories.map(s => s.id), selected_article_ids: selected.map(s => s.id),
            excluded_article_ids: Object.keys(picks).filter(id => !picks[id]),
            minutes: preferences.minutes, mode: preferences.mode === 'panel' ? 'debate' : 'presenter',
          })
        const ids = segments.map(segment => segment.article_ids[0])
        if (!ids.length) throw new Error('No stories are available for this briefing.')
        const headlines = new Map(stories.map(story => [story.id, story]))
        for (const headline of await lookupHeadlines(ids.filter(id => !headlines.has(id)))) {
          headlines.set(headline.id, headline)
        }
        const lineup = ids.map(id => headlines.get(id) ?? { id, title: id, topic: '', summary: '' })
        setLaunch({ ...origin, prepared: {
          stories: lineup, preferences: { ...preferences, profile: preferences.profile.trim() || profile },
          plan: { segments, hosts, tones },
        } })
      } catch (error) {
        setBriefingError(error instanceof Error ? error.message : 'The briefing could not be generated.')
      } finally {
        setPreparing(false)
      }
      return
    }
    let context = null
    try { context = new AudioContext(); void context.resume().catch(() => {}) } catch { /* Native playback still works. */ }
    setLaunch({ ...origin, context })
  }

  return <div className="workspace">
    <a className="skip-link" href="#workspace-content">Skip to content</a>
    <header className={view === 'feed' ? 'masthead with-search' : 'masthead'}>
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
      <button ref={trigger} type="button" className="agent-launch" aria-label="Start agent discussion" title="Start agent discussion" disabled={preparing} onFocus={() => void loadPlayer()} onPointerEnter={() => void loadPlayer()} onClick={start}>
        <Orb />{selected.length > 0 && <small aria-hidden="true">{selected.length}</small>}
      </button>
      <nav aria-label="Workspace" className="workspace-nav">
        {(['feed', 'briefings'] as const).map(id => <button type="button" key={id} aria-current={view === id ? 'page' : undefined} onClick={() => { setView(id); window.scrollTo(0, 0) }}>
          <Icon name={id === 'feed' ? 'feed' : 'discussion'} />{id === 'feed' ? 'News feed' : 'Briefings'}
        </button>)}
      </nav>
    </div>
    <section id="workspace-content" tabIndex={-1} aria-label="News workspace" className="page-width">
      {briefingError && <p className="selection-notice" role="alert">{briefingError}</p>}
      {view === 'briefings' ? <Briefings key={historyVersion} owner={owner} /> : <>
        {submittedQuestion && <div role="status" className="search-result"><strong>{submittedQuestion}</strong><p>Article search is a preview. It hasn’t searched the dataset.</p></div>}
        <div className="page-title"><h1>Your newsroom</h1>
          {!waitingForCatalog && <span className="edition-note">{userId ? 'Personalized edition' : sample ? 'Sample edition' : 'News edition'}</span>}
        </div>
        <section className="briefing-cover" aria-labelledby="cover-title"><AudioRibbon /><div className="cover-copy">
          <span className="cover-meta"><Icon name="discussion" /> English · Synthetic narration</span>
          <h2 id="cover-title">The daily <em>briefing</em></h2>
          <p>Swipe right to include a headline or left to skip it. Tap the host orb to discuss your lineup.</p>
        </div></section>
        {demoListeners && demoListeners.length > 0 && <div className="field max-w-md">
          <label htmlFor="demo-listener">Listener</label>
          <select id="demo-listener" value={demoListener} disabled={preparing} onChange={event => {
            const id = event.target.value
            const minutes = demoListeners.find(listener => listener.id === id)?.minutes
            setPreferences(previous => ({ ...previous, profile: '', minutes: minutes ? Math.min(minutes, 15) : 4 }))
            setBriefingError('')
            setDemoListener(id)
          }}>
            {demoListeners.map(listener => <option key={listener.id} value={listener.id}>{listener.name}</option>)}
            <option value="">Standard feed</option>
          </select>
        </div>}
        <section className="lineup" aria-label="Selected headlines">
          <div className="lineup-label"><h2>Your lineup</h2><span>{selected.length} / 6</span></div>
          {selected.length ? <ol>{selected.map(s => <li key={s.id}><button disabled={preparing} onClick={() => choose(s, false)} aria-label={'Remove ' + s.title}>{s.title} <span aria-hidden="true">×</span></button></li>)}</ol> : <p>Included headlines appear here.</p>}
          {preparing && <p role="status">Generating your briefing…</p>}
          <span role="status" className={notice.startsWith('Your lineup') || notice.startsWith('Choose') || notice.startsWith('Check') ? 'selection-notice' : 'sr-only'}>{notice}</span>
        </section>
        {online && <details id="preferences" className="briefing-settings"><summary>Briefing settings</summary>
          {sample && <label className="field">Playback<select value={recorded ? 'sample' : 'live'} onChange={e => setRecorded(e.target.value === 'sample')}><option value="sample">Recorded sample</option><option value="live">Generate a personal briefing</option></select></label>}
          {!recorded && <div className="setup-fields">
            <label className="field">{userId ? 'Listener instructions (optional)' : 'Listener profile'}<textarea value={preferences.profile} placeholder={userId ? 'Use the saved listener profile' : defaultProfile} disabled={preparing} maxLength={3000} onChange={e => setPreferences({ ...preferences, profile: e.target.value })} /></label>
            <div className="format-fields"><label className="field">Format<select value={preferences.mode} disabled={preparing} onChange={e => setPreferences({ ...preferences, mode: e.target.value as Preferences['mode'] })}><option value="panel">Expert panel</option><option value="presenter">Presenter</option></select></label>
            <label className="field">Minutes<input type="number" min="1" max="15" value={preferences.minutes} disabled={preparing} onChange={e => setPreferences({ ...preferences, minutes: Number(e.target.value) })} /></label></div>
          </div>}
        </details>}
        <section aria-labelledby="stories-title">
          <div className="section-title"><h2 id="stories-title">{userId ? 'Recommended articles' : 'Top stories'}</h2><span>{stories.length} {userId ? 'articles' : 'stories'}</span></div>
          {feedError && <p role="alert">{feedError}</p>}
          <ul className="story-grid selectable-stories">{stories.map(story => <li key={story.id}>
            <article data-choice={picks[story.id] === undefined ? undefined : picks[story.id] ? 'include' : 'skip'}
              onPointerDown={e => {
                if (preparing || !e.isPrimary || e.button !== 0 || (e.target as HTMLElement).closest('button')) return
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
              {story.image && <img className="story-photo" src={'/edition/' + story.image + '.webp'} alt="" draggable={false} />}
              <p className="topic">{story.topic}</p><h3>{story.title}</h3><p className="story-summary">{story.summary}</p>
              <div className="story-vote"><button disabled={preparing} aria-pressed={picks[story.id] === false} onClick={() => choose(story, false)}>← {picks[story.id] === false ? 'Skipped' : 'Skip'}</button>
              <button disabled={preparing} aria-pressed={picks[story.id] === true} onClick={() => choose(story, true)}>{picks[story.id] ? 'Included' : 'Include'} →</button></div>
            </article></li>)}</ul>
          {!stories.length && !feedError && <p role="status">{online ? 'No headlines are available.' : userId ? 'Loading recommendations…' : 'Loading headlines…'}</p>}
        </section>
      </>}
    </section>
    {launch && <Suspense fallback={<p className="session-loading" role="status">Opening briefing…</p>}><PanelPlayer owner={owner}
      stories={launch.prepared?.stories ?? selected}
      preferences={launch.prepared?.preferences ?? { ...preferences, profile: preferences.profile.trim() || defaultProfile }}
      plan={launch.prepared?.plan} recorded={!launch.prepared && recorded} origin={launch} onClose={() => {
      if (launch.context && launch.context.state !== 'closed') void launch.context.close().catch(() => {})
      setHistoryVersion(v => v + 1)
      setLaunch(null)
      requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }))
    }} /></Suspense>}
  </div>
}
