import { useEffect, useRef, useState } from 'react'
import { audioUrl, historyStore, sampleArticle } from './briefings'
import type { Briefing, Card, Segment } from './briefings'

export function Sources({ cards, compact = false }: { cards: Card[]; compact?: boolean }) {
  if (!cards.length) return null
  return <aside className={compact ? 'fact-check glass-card' : 'sources'} aria-label={compact ? 'Fact check' : 'Supporting sources'}>
    <h3>{compact ? 'Fact check' : 'Supporting sources'}</h3>
    {compact && <p className="fact-claim">{cards[0].claim}</p>}
    {cards.map((s, i) => {
      const url = s.url ?? (sampleArticle(s.id) ? '/sample-source?id=' + encodeURIComponent(s.id) : null)
      return <blockquote key={i}>
      <cite>{url ? <a href={url} target="_blank" rel="noreferrer">{s.publisher}</a> : s.publisher}</cite>
      {compact ? <details><summary>Source excerpt</summary><p lang={s.language}>“{s.quote}”</p></details>
        : <><p><strong>{s.claim}</strong></p><p lang={s.language}>“{s.quote}”</p></>}
      </blockquote>
    })}
  </aside>
}

function SavedConversation({ briefing }: { briefing: Briefing }) {
  const turns = briefing.entries.filter((e): e is Segment => 'sources' in e)
  const [turn, setTurn] = useState(turns[0])
  const audio = useRef<HTMLAudioElement>(null)
  function play(next: Segment) {
    const player = audio.current
    if (!player) return
    setTurn(next)
    player.src = audioUrl(next)
    void player.play().catch(() => {})
  }
  return <div className="history-lines">
    {turn && <><audio ref={audio} controls preload="none" aria-label="Replay briefing" src={audioUrl(turns[0])} onEnded={() => {
      const next = turns[turns.indexOf(turn) + 1]
      if (next) play(next)
    }} /><Sources cards={turn.sources} /></>}
    {briefing.entries.map(e => <article key={e.id}>
      <h3>{e.speaker}</h3><p>{e.text}</p>
      {'sources' in e && <button className="replay-turn" onClick={() => play(e)}>Play this turn</button>}
    </article>)}
  </div>
}

export function Briefings({ owner }: { owner: string }) {
  const [sessions, setSessions] = useState<Briefing[]>([])
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState('')
  useEffect(() => {
    let mounted = true
    historyStore(owner).then(rows => {
      if (mounted) { setSessions(rows.sort((a, b) => b.date.localeCompare(a.date))); setReady(true) }
    }).catch(() => { if (mounted) setError('Your browser could not open the saved briefings.') })
    return () => { mounted = false }
  }, [owner])
  return <>
    <div className="page-title"><h1>Briefings</h1></div>
    {error && <p role="alert">{error}</p>}
    {!ready && !error && <p role="status">Opening your briefings…</p>}
    {ready && !sessions.length && <div className="history-empty"><h2>Your conversations live here.</h2><p>Choose a headline in News feed and start a briefing with the host. You can return here to listen again or read the sources.</p></div>}
    {sessions.map(s => <section className="history-item glass-card" key={s.id}>
      <button className="history-title" aria-expanded={open === s.id} onClick={() => setOpen(open === s.id ? '' : s.id)}>
        <span>{new Date(s.date).toLocaleString()} · {s.sample ? 'Sample briefing' : 'Briefing'}</span>
        <strong>{s.titles.join(' · ')}</strong>
      </button>
      {open === s.id && <div><SavedConversation briefing={s} /><button className="remove-briefing" onClick={async () => {
        if (!window.confirm('Delete this briefing and its audio from this device?')) return
        try { await historyStore(owner, s.id); setSessions(rows => rows.filter(row => row.id !== s.id)); setOpen('') }
        catch { setError('Could not delete this briefing. Try again.') }
      }}>Delete briefing</button></div>}
    </section>)}
  </>
}
