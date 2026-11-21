// Saved briefings, newest first and grouped by day. Each card lists its stories and who spoke; opening it shows the
// transcript as a conversation, one story at a time if the listener picks one, with every line's sources linked.
// Closing a card, from its header, the Collapse button under the transcript or Escape, brings the list back into
// view at that card, so a long transcript never leaves the listener stranded far down the page.
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Icon } from './Brand'
import { historyStore, sampleArticle } from './briefings'
import type { Briefing, Card, Entry, Segment } from './briefings'

const isTurn = (entry: Entry): entry is Segment => 'sources' in entry
const shownStories = 3

function sourceUrl(card: Card) {
  if (card.url && /^https?:\/\//i.test(card.url)) return card.url
  return sampleArticle(card.id) ? '/sample-source?id=' + encodeURIComponent(card.id) : null
}

// One entry per article, since a line often cites the same one for several claims.
function articles(cards: Card[]) {
  const seen = new Map<string, Card>()
  for (const card of cards) {
    const key = card.url || card.id || card.publisher
    if (!seen.has(key)) seen.set(key, card)
  }
  return [...seen.values()]
}

// Each voice keeps one colour across the page; the host always wears the brand's gold.
const tints = ['#d9a59b', '#86c7b6', '#b9acd9', '#9fc3dc', '#adc493', '#e0b483']
function tint(name: string, host: string) {
  if (name === host) return 'var(--gold)'
  if (name === 'Listener') return 'var(--stone)'
  let hash = 0
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return tints[hash % tints.length]
}

function Avatar({ name, host }: { name: string; host: string }) {
  const letters = name === 'Listener' ? 'You' : name.split(/\s+/).map(word => word[0]).join('').slice(0, 2).toUpperCase()
  return <span className="speaker-avatar" style={{ '--speaker': tint(name, host) } as CSSProperties} aria-hidden="true">{letters}</span>
}

function dayLabel(date: Date) {
  const today = new Date()
  const midnight = (day: Date) => new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime()
  const days = Math.round((midnight(today) - midnight(date)) / 86_400_000)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  return date.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long',
    year: date.getFullYear() === today.getFullYear() ? undefined : 'numeric' })
}

function overview(briefing: Briefing) {
  const turns = briefing.entries.filter(isTurn)
  const host = turns.find(turn => turn.host)?.host ?? ''
  const voices = [...new Set([host, ...turns.map(turn => turn.speaker)].filter(Boolean))]
  return { host, voices, turns: turns.length, questions: briefing.entries.length - turns.length,
    sources: articles(turns.flatMap(turn => turn.sources)).length }
}

const plural = (count: number, word: string, words = word + 's') => `${count} ${count === 1 ? word : words}`

function transcriptText(briefing: Briefing) {
  const lines = briefing.entries.map(entry => {
    const cited = isTurn(entry) ? articles(entry.sources).map(card => [card.publisher, sourceUrl(card)].filter(Boolean).join(' ')) : []
    return `${entry.speaker === 'Listener' ? 'You' : entry.speaker}: ${entry.text}` + (cited.length ? `\n  Sources: ${cited.join('; ')}` : '')
  })
  return [briefing.titles.join(' · '), new Date(briefing.date).toLocaleString(), '', ...lines].join('\n\n')
}

function Transcript({ briefing, host }: { briefing: Briefing; host: string }) {
  const [story, setStory] = useState('')
  // A listener's question belongs to the story being discussed when they asked it.
  let current = ''
  const rows = briefing.entries.map(entry => {
    if (isTurn(entry) && entry.story) current = entry.story
    return { entry, story: current }
  })
  const stories = [...new Set(rows.map(row => row.story).filter(Boolean))]
  const shown = story ? rows.filter(row => row.story === story) : rows

  return <>
    {stories.length > 1 && <div className="transcript-filter" role="group" aria-label="Show one story">
      <button type="button" aria-pressed={!story} onClick={() => setStory('')}>All stories</button>
      {stories.map((name, index) => <button type="button" key={name} aria-pressed={story === name} title={name}
        onClick={() => setStory(story === name ? '' : name)}><span className="transcript-filter-number">{index + 1}</span>{name}</button>)}
    </div>}
    <ol className="transcript">
      {shown.map(({ entry, story: from }, index) => {
        const listener = !isTurn(entry)
        const cited = isTurn(entry) ? articles(entry.sources) : []
        return <li key={entry.id} className="transcript-turn" data-listener={listener || undefined}>
          {!story && stories.length > 1 && from && from !== shown[index - 1]?.story &&
            <p className="transcript-story"><span>Story {stories.indexOf(from) + 1}</span>{from}</p>}
          <div className="transcript-line">
            <Avatar name={entry.speaker} host={host} />
            <div className="transcript-body">
              <p className="transcript-speaker">{listener ? 'You asked' : entry.speaker}
                {entry.speaker === host && <span className="transcript-role">Host</span>}</p>
              <p className="transcript-text">{entry.text}</p>
              {cited.length > 0 && <p className="transcript-sources" aria-label="Sources">
                {cited.map(card => {
                  const url = sourceUrl(card)
                  return url ? <a key={card.id || card.publisher} href={url} target="_blank" rel="noreferrer" title={card.claim}>{card.publisher}</a>
                    : <span key={card.id || card.publisher} title={card.claim}>{card.publisher}</span>
                })}
              </p>}
            </div>
          </div>
        </li>
      })}
    </ol>
  </>
}

function BriefingCard({ briefing, open, onToggle, onDelete }: {
  briefing: Briefing; open: boolean; onToggle: () => void; onDelete: () => Promise<void>
}) {
  const body = useId()
  const card = useRef<HTMLLIElement>(null)
  const toggle = useRef<HTMLButtonElement>(null)
  const wasOpen = useRef(open)
  const [copied, setCopied] = useState<'' | 'done' | 'failed'>('')
  const [confirming, setConfirming] = useState(false)
  const { host, voices, turns, questions, sources } = overview(briefing)
  const date = new Date(briefing.date)
  const more = briefing.titles.length - shownStories
  // When any listed story has a picture, every row gets a thumbnail slot so the headlines line up.
  const pictured = briefing.titles.slice(0, shownStories).some(title => briefing.pictures?.[title])

  useEffect(() => { if (!open) setConfirming(false) }, [open])

  // After the card opens or closes (closing another card can move this one too), bring its header back into view if
  // it has scrolled off the screen.
  useLayoutEffect(() => {
    if (wasOpen.current === open) return
    wasOpen.current = open
    const top = card.current!.getBoundingClientRect().top
    // The card's scroll margin keeps its day heading, or the end of the card above, in sight.
    if (top < parseFloat(getComputedStyle(card.current!).scrollMarginTop) - 4 || top > innerHeight - 120) card.current!.scrollIntoView({ block: 'start',
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
  }, [open])

  function collapse() {
    toggle.current?.focus({ preventScroll: true })
    onToggle()
  }
  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(''), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  return <li className="briefing-card glass-card" data-open={open || undefined} ref={card}
    onKeyDown={event => { if (open && event.key === 'Escape') { event.stopPropagation(); collapse() } }}>
    <button type="button" className="briefing-toggle" aria-expanded={open} aria-controls={body} onClick={onToggle} ref={toggle}>
      <span className="briefing-meta">
        <time dateTime={briefing.date}>{date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
        {briefing.sample && <span className="briefing-badge">Sample</span>}
        <span className="briefing-voices">{voices.slice(0, 5).map(name => <Avatar key={name} name={name} host={host} />)}</span>
        <Icon name="chevron" />
      </span>
      <span className="briefing-stories">
        {briefing.titles.slice(0, shownStories).map((title, index) => {
          const picture = briefing.pictures?.[title]
          return <span className="briefing-story" key={title} data-pictured={pictured || undefined}>
            <span className="briefing-number" aria-hidden="true">{index + 1}</span>
            {pictured && (picture
              ? <img className="briefing-thumb" src={picture} alt="" loading="lazy" decoding="async" draggable={false}
                  referrerPolicy="no-referrer" onError={event => { event.currentTarget.style.visibility = 'hidden' }} />
              : <span className="briefing-thumb" aria-hidden="true" />)}
            <span className="briefing-headline">{title}</span>
          </span>
        })}
        {more > 0 && <span className="briefing-more">+{plural(more, 'more story', 'more stories')}</span>}
      </span>
      <span className="briefing-stats">
        {[plural(briefing.titles.length, 'story', 'stories'), plural(turns, 'line'), plural(sources, 'source'),
          questions ? plural(questions, 'question') : '', voices.length ? voices.join(', ') : ''].filter(Boolean).join(' · ')}
      </span>
    </button>
    {open && <div className="briefing-body" id={body}>
      <Transcript briefing={briefing} host={host} />
      <div className="briefing-actions">
        <button type="button" className="briefing-collapse" onClick={collapse}><Icon name="chevron" />Collapse</button>
        <button type="button" onClick={() => navigator.clipboard.writeText(transcriptText(briefing))
          .then(() => setCopied('done'), () => setCopied('failed'))}>
          <Icon name={copied === 'done' ? 'check' : 'copy'} />{copied === 'done' ? 'Copied' : copied === 'failed' ? 'Could not copy' : 'Copy transcript'}
        </button>
        {confirming
          ? <span className="briefing-confirm" role="group" aria-label="Delete this transcript from this device?">
              <span>Delete from this device?</span>
              <button type="button" className="briefing-delete" onClick={() => void onDelete()}>Delete</button>
              <button type="button" onClick={() => setConfirming(false)}>Keep</button>
            </span>
          : <button type="button" className="briefing-delete" onClick={() => setConfirming(true)}><Icon name="trash" />Delete</button>}
      </div>
    </div>}
  </li>
}

export function Briefings({ owner }: { owner: string }) {
  const [sessions, setSessions] = useState<Briefing[]>([])
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState('')
  const [query, setQuery] = useState('')

  useEffect(() => {
    let mounted = true
    historyStore(owner).then(rows => {
      if (mounted) { setSessions(rows.sort((a, b) => b.date.localeCompare(a.date))); setReady(true) }
    }).catch(() => { if (mounted) setError('Your browser could not open the saved briefings.') })
    return () => { mounted = false }
  }, [owner])

  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  const matches = words.length ? sessions.filter(briefing => {
    const text = [...briefing.titles, ...briefing.entries.flatMap(entry => [entry.speaker, entry.text])].join(' ').toLowerCase()
    return words.every(word => text.includes(word))
  }) : sessions
  const days: { label: string; rows: Briefing[] }[] = []
  for (const briefing of matches) {
    const label = dayLabel(new Date(briefing.date))
    if (days.at(-1)?.label === label) days.at(-1)!.rows.push(briefing)
    else days.push({ label, rows: [briefing] })
  }

  async function remove(id: string) {
    try {
      await historyStore(owner, id)
      setSessions(rows => rows.filter(row => row.id !== id))
      setOpen('')
    } catch { setError('Could not delete this briefing. Try again.') }
  }

  return <>
    <div className="page-title briefings-title">
      <h1>Briefings</h1>
      {ready && sessions.length > 0 && <span className="muted">{plural(sessions.length, 'briefing')} saved on this device</span>}
    </div>
    {error && <p role="alert">{error}</p>}
    {!ready && !error && <p role="status">Opening your briefings…</p>}
    {ready && !sessions.length && <div className="history-empty"><h2>Your transcripts live here.</h2><p>Start a briefing from News feed. Key discussion transcripts and source links are saved here.</p></div>}
    {sessions.length > 1 && <label className="briefing-search">
      <Icon name="search" />
      <span className="sr-only">Search your briefings</span>
      <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search headlines, voices and transcripts" />
    </label>}
    {words.length > 0 && <p className="briefing-results" role="status">
      {matches.length ? `${plural(matches.length, 'briefing')} found` : 'No briefing mentions that.'}</p>}
    {days.map(day => <section className="briefing-day" key={day.label} aria-label={day.label}>
      <h2>{day.label}</h2>
      <ol className="briefing-list">
        {day.rows.map(briefing => <BriefingCard key={briefing.id} briefing={briefing} open={open === briefing.id}
          onToggle={() => setOpen(open === briefing.id ? '' : briefing.id)} onDelete={() => remove(briefing.id)} />)}
      </ol>
    </section>)}
  </>
}
