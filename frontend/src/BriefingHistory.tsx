import { useEffect, useState } from 'react'
import { historyStore, sampleArticle } from './briefings'
import type { Briefing, Card } from './briefings'

function Sources({ cards }: { cards: Card[] }) {
  if (!cards.length) return null
  const sources = cards.filter((s, i) => cards.findIndex(other => (other.url || other.id) === (s.url || s.id)) === i)
  return <p className="history-sources" aria-label="Supporting sources">
    Sources: {sources.map((s, i) => {
      const url = s.url ?? (sampleArticle(s.id) ? '/sample-source?id=' + encodeURIComponent(s.id) : null)
      return <span key={i}>{i > 0 && ' · '}{url ? <a href={url} target="_blank" rel="noreferrer">{s.publisher}</a> : s.publisher}</span>
    })}
  </p>
}

function SavedConversation({ briefing }: { briefing: Briefing }) {
  return <div className="history-lines">
    {briefing.entries.map(e => <article key={e.id}>
      <h3>{e.speaker}</h3><p>{e.text}</p>
      {'sources' in e && <Sources cards={e.sources} />}
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
    {ready && !sessions.length && <div className="history-empty"><h2>Your transcripts live here.</h2><p>Start a briefing from News feed. Key discussion transcripts and source links are saved here.</p></div>}
    {sessions.map(s => <section className="history-item glass-card" key={s.id}>
      <button className="history-title" aria-expanded={open === s.id} onClick={() => setOpen(open === s.id ? '' : s.id)}>
        <span>{new Date(s.date).toLocaleString()} · {s.sample ? 'Sample briefing' : 'Briefing'}</span>
        <strong>{s.titles.join(' · ')}</strong>
      </button>
      {open === s.id && <div><SavedConversation briefing={s} /><button className="remove-briefing" onClick={async () => {
        if (!window.confirm('Delete this transcript from this device?')) return
        try { await historyStore(owner, s.id); setSessions(rows => rows.filter(row => row.id !== s.id)); setOpen('') }
        catch { setError('Could not delete this briefing. Try again.') }
      }}>Delete briefing</button></div>}
    </section>)}
  </>
}
