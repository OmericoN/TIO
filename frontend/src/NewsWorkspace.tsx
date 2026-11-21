import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { AudioRibbon, Brand, Icon } from './Brand'
import { Briefings } from './BriefingHistory'
import { Orb } from './Orb'
import { listenerChoicesKey, loadListenerChoices, loadSettings, Profile } from './Profile'
import type { ListenerChoices } from './Profile'
import { sampleHeadlines } from './briefings'
import type { Headline, Preferences } from './briefings'

const loadPlayer = () => import('./PanelPlayer').then(module => ({ default: module.PanelPlayer }))
const PanelPlayer = lazy(loadPlayer)
const defaultProfile = 'Explain what changed and why it matters to a general news listener.'
const swipeTravel = 16

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

export function NewsWorkspace({ owner, onboarding, listenerId: userId }: { owner: string; onboarding?: unknown; listenerId?: string }) {
  const [feedError, setFeedError] = useState('')
  const [view, setView] = useState<'feed' | 'briefings' | 'profile'>('feed')
  const [stories, setStories] = useState<Headline[]>([])
  const [historyVersion, setHistoryVersion] = useState(0)
  const [online, setOnline] = useState(false)
  const [savedSettings] = useState(() => loadSettings(owner))
  const [recorded, setRecorded] = useState(savedSettings?.recorded ?? true)
  const [choices, setChoices] = useState(() => loadListenerChoices(owner, onboarding))
  const [storageError, setStorageError] = useState(false)
  const interests = [...choices.topics, choices.otherInterests.trim()].filter(Boolean)
  const preferences: Preferences = {
    profile: interests.length ? `Explain what changed and why it matters to a listener interested in ${interests.join(', ')}.` : savedSettings?.preferences.profile ?? defaultProfile,
    minutes: savedSettings?.preferences.minutes ?? 4,
  }
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [revealedId, setRevealedId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [agentNotice, setAgentNotice] = useState('')
  const [question, setQuestion] = useState('')
  const [search, setSearch] = useState<{ query: string; status: 'loading' | 'done' | 'error'; message?: string } | null>(null)
  const [launch, setLaunch] = useState<{ x: number; y: number; context: AudioContext | null; lineup: Headline[]
    listener?: { id: string; articleIds: string[]; selectedIds: string[] } } | null>(null)
  const feed = useRef<Headline[]>([])
  const searchRequest = useRef<AbortController | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const agentControl = useRef<HTMLDivElement>(null)
  const swipe = useRef<{ pointerId: number; x: number; y: number; content: HTMLElement; open: boolean; animate: boolean } | null>(null)
  const selected = selectedIds.map(id => stories.find(story => story.id === id)).filter(story => story !== undefined)

  // Signed-in listeners get their ranked feed. The demo uses the shared story catalog.
  useEffect(() => {
    const controller = new AbortController()
    setStories([])
    feed.current = []
    searchRequest.current?.abort()
    setSearch(null)
    setSelectedIds([])
    setOnline(false)
    setFeedError('')
    async function load() {
      try {
        if (userId) {
          const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(90_000)])
          const recommendations = await requestJson<{ article_ids: string[] }>('/api/recommend', signal, { user_id: userId })
          const headlines = await lookupHeadlines(recommendations.article_ids, signal)
          if (controller.signal.aborted) return
          feed.current = headlines
          setStories(headlines)
          setRecorded(false)
        } else {
          const catalog = await requestJson<{ sample: boolean; stories: Headline[] }>('/api/panel/stories',
            AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]))
          if (controller.signal.aborted) return
          if (!Array.isArray(catalog.stories)) throw new Error('The story catalog is unavailable.')
          feed.current = catalog.sample ? sampleHeadlines : catalog.stories
          setStories(feed.current)
          setRecorded(catalog.sample && (savedSettings?.recorded ?? true))
        }
        setOnline(true)
      } catch (error) {
        if (controller.signal.aborted) return
        if (userId) setFeedError(error instanceof Error ? error.message : 'Recommendations could not be loaded.')
        else { feed.current = sampleHeadlines; setStories(sampleHeadlines); setRecorded(true) }
      }
    }
    void load()
    return () => controller.abort()
  }, [userId])

  // Keep picked headlines visible, so a search never drops a pick.
  function showWithPicks(results: Headline[]) {
    setStories(current => {
      const ids = new Set(results.map(story => story.id))
      return [...current.filter(story => selectedIds.includes(story.id) && !ids.has(story.id)), ...results]
    })
  }

  async function runSearch(query: string) {
    searchRequest.current?.abort()
    const controller = new AbortController()
    searchRequest.current = controller
    setSearch({ query, status: 'loading' })
    try {
      let results: Headline[]
      if (userId) {
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(90_000)])
        const found = await requestJson<{ article_ids: string[] }>('/api/recommend', signal, { user_id: userId, focus: query, limit: 30 })
        results = await lookupHeadlines(found.article_ids, signal)
      } else {
        const words = query.toLowerCase().split(/\s+/).filter(Boolean)
        results = feed.current.filter(story => words.every(word => `${story.title} ${story.topic} ${story.summary}`.toLowerCase().includes(word)))
      }
      if (controller.signal.aborted) return
      showWithPicks(results)
      setSearch({ query, status: 'done',
        message: results.length ? `${results.length} ${results.length === 1 ? 'article' : 'articles'} found` : 'No articles match this search.' })
    } catch (error) {
      if (controller.signal.aborted) return
      setSearch({ query, status: 'error', message: error instanceof Error ? error.message : 'Search is unavailable.' })
    }
  }

  function clearSearch() {
    searchRequest.current?.abort()
    setSearch(null)
    showWithPicks(feed.current)
  }

  useEffect(() => {
    if (!agentNotice) return
    function dismissOutside(event: PointerEvent) {
      if (!agentControl.current?.contains(event.target as Node)) setAgentNotice('')
    }
    function dismissEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setAgentNotice('')
    }
    document.addEventListener('pointerdown', dismissOutside)
    document.addEventListener('keydown', dismissEscape)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside)
      document.removeEventListener('keydown', dismissEscape)
    }
  }, [agentNotice])

  function toggleStory(story: Headline) {
    const picked = selectedIds.includes(story.id)
    if (!picked && selectedIds.length >= 6) {
      const message = 'Your lineup holds six headlines. Remove one before adding another.'
      setNotice(message)
      setAgentNotice(message)
      return
    }
    setRevealedId(null)
    setAgentNotice('')
    setSelectedIds(previous => picked ? previous.filter(id => id !== story.id) : [...previous, story.id])
    setNotice((picked ? 'Removed from briefing: ' : 'Added to briefing: ') + story.title)
  }

  function startSwipe(event: ReactPointerEvent<HTMLElement>, open: boolean) {
    if (!event.isPrimary || event.pointerType !== 'touch' || (event.target as Element).closest('button')) return
    const content = event.currentTarget.querySelector<HTMLElement>('.story-content')!
    swipe.current = {
      pointerId: event.pointerId, x: event.clientX, y: event.clientY, content, open,
      animate: !matchMedia('(prefers-reduced-motion: reduce)').matches,
    }
  }

  function moveSwipe(event: ReactPointerEvent<HTMLElement>) {
    const start = swipe.current
    if (!start?.animate || event.pointerId !== start.pointerId) return
    const dx = event.clientX - start.x
    if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(event.clientY - start.y)) return
    const offset = dx + (start.open ? -swipeTravel : 0)
    start.content.dataset.swiping = ''
    start.content.style.translate = `${Math.max(-swipeTravel, Math.min(0, offset))}px`
  }

  function finishSwipe(event: ReactPointerEvent<HTMLElement>, storyId?: string) {
    const start = swipe.current
    if (!start || event.pointerId !== start.pointerId) return
    swipe.current = null
    delete start.content.dataset.swiping
    start.content.style.removeProperty('translate')
    const dx = event.clientX - start.x
    if (storyId && Math.abs(dx) >= 32 && Math.abs(dx) > 1.5 * Math.abs(event.clientY - start.y)) {
      if (dx < 0) setRevealedId(storyId)
      else if (start.open) setRevealedId(null)
    }
  }

  // Picks are optional: a listener's briefing is planned to fill the chosen minutes from their feed, and the shared
  // catalog falls back to its top stories, one per two minutes.
  function start() {
    if (!stories.length) {
      const message = feedError ? 'The feed could not load, so there are no articles to discuss yet.' : 'The feed is still loading. Try again in a moment.'
      setNotice(message)
      setAgentNotice(message)
      return
    }
    setAgentNotice('')
    const rect = trigger.current!.getBoundingClientRect()
    let context = null
    try { context = new AudioContext(); void context.resume().catch(() => {}) } catch { /* Native playback still works. */ }
    const lineup = selected.length ? selected : stories.slice(0, Math.min(6, Math.max(1, Math.floor(preferences.minutes / 2))))
    setLaunch({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, context,
      lineup: userId ? selected : lineup,
      listener: userId ? { id: userId, articleIds: stories.map(story => story.id), selectedIds } : undefined })
  }

  function saveChoices(next: ListenerChoices) {
    setChoices(next)
    try {
      localStorage.setItem(listenerChoicesKey(owner), JSON.stringify(next))
      setStorageError(false)
    } catch { setStorageError(true) }
  }

  return <div className="workspace">
    <a className="skip-link" href="#workspace-content">Skip to content</a>
    {view === 'feed' && <header className="masthead with-search">
      <AudioRibbon />
      <div className="masthead-inner"><div className="brand-lockup"><Brand /></div></div>
    </header>}
    {view === 'feed' && <div className="search-rail">
      <form className="article-search" role="search" aria-label="Article search" onSubmit={event => {
        event.preventDefault()
        const query = question.trim()
        if (query.length < 2) return
        void runSearch(query)
        window.scrollTo(0, 0)
      }}>
        <Icon name="search" />
        <label htmlFor="article-question" className="sr-only">Search the articles</label>
        <input id="article-question" type="search" enterKeyHint="search" placeholder="Search the news…" value={question} maxLength={500} onChange={event => setQuestion(event.target.value)} />
        <button type="submit" aria-label="Search" disabled={question.trim().length < 2}><Icon name="arrow" /></button>
      </form>
    </div>}
    <div className="workspace-dock">
      <div ref={agentControl} className="agent-control" onPointerLeave={event => {
        if (event.pointerType !== 'touch') setAgentNotice('')
      }} onBlur={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setAgentNotice('')
      }}>
      {agentNotice && <div id="agent-notice" className="agent-notice" role="tooltip">{agentNotice}</div>}
      <button ref={trigger} type="button" className="agent-launch" aria-label="Start agent discussion"
        aria-describedby={agentNotice ? 'agent-notice' : undefined}
        onFocus={() => void loadPlayer()} onPointerEnter={() => void loadPlayer()} onClick={start}>
        <Orb />{selected.length > 0 && <small aria-hidden="true">{selected.length}</small>}
        {agentNotice && <span className="agent-info" aria-hidden="true">i</span>}
      </button>
      </div>
      <nav aria-label="Workspace" className="workspace-nav">
        {(['feed', 'briefings', 'profile'] as const).map(id => <button type="button" key={id} aria-current={view === id ? 'page' : undefined} onClick={() => { setView(id); window.scrollTo(0, 0) }}>
          <Icon name={id === 'feed' ? 'feed' : id === 'profile' ? 'profile' : 'discussion'} />{id === 'feed' ? 'News feed' : id === 'profile' ? 'Profile' : 'Briefings'}
        </button>)}
      </nav>
    </div>
    <section id="workspace-content" tabIndex={-1} aria-label="News workspace" className="page-width">
      {view === 'profile' ? <Profile choices={choices} storageError={storageError} onChange={saveChoices} briefingOpen={!!launch} />
        : view === 'briefings' ? <Briefings key={historyVersion} owner={owner} /> : <>
        {search && <div role="status" className="search-result"><strong>{search.query}</strong>
          <p>{search.status === 'loading' ? 'Searching the articles…' : search.message}</p>
          <button type="button" className="search-clear" onClick={() => { clearSearch(); setQuestion('') }}>Clear search</button>
        </div>}
        <h1 className="sr-only">News feed</h1>
        <span role="status" className="sr-only">{notice}</span>
        <section aria-labelledby="stories-title">
          <div className="section-title"><h2 id="stories-title">{search ? 'Search results' : userId ? 'Recommended articles' : 'Top articles'}</h2></div>
          <ul className="story-grid selectable-stories">{stories.map(story => {
            const picked = selectedIds.includes(story.id)
            const revealed = revealedId === story.id
            return <li key={story.id}><article className="glass-card" data-selected={picked} data-swipe-open={revealed}
              onPointerDown={event => startSwipe(event, revealed)} onPointerMove={moveSwipe}
              onPointerUp={event => finishSwipe(event, story.id)} onPointerCancel={finishSwipe} onLostPointerCapture={finishSwipe}>
              <button type="button" className="story-select" aria-label={`Select article: ${story.title}`} aria-pressed={picked} onClick={() => toggleStory(story)}>
                <Icon name={picked ? 'check' : 'plus'} />
              </button>
              <div className="story-content">
                {typeof story.thumb_url === 'string' && /^https?:\/\//i.test(story.thumb_url) &&
                  <img className="story-photo" src={story.thumb_url} alt="" draggable={false} loading="lazy"
                    referrerPolicy="no-referrer" onError={event => { event.currentTarget.hidden = true }} />}
                <p className="topic">{story.topic}</p><h3>{story.title}</h3><p className="story-summary">{story.summary}</p>
              </div>
            </article></li>
          })}</ul>
          {feedError && <p role="alert">{feedError}</p>}
          {!stories.length && !feedError && <p role="status">{online ? 'No indexed headlines are available.' : userId ? 'Loading recommendations…' : 'Loading headlines…'}</p>}
        </section>
      </>}
    </section>
    {launch && <Suspense fallback={<p className="session-loading" role="status">Opening briefing…</p>}><PanelPlayer owner={owner} stories={launch.lineup} listener={launch.listener} preferences={preferences} recorded={recorded} origin={launch} onClose={() => {
      if (launch.context && launch.context.state !== 'closed') void launch.context.close().catch(() => {})
      setHistoryVersion(v => v + 1)
      setLaunch(null)
      requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }))
    }} /></Suspense>}
  </div>
}
