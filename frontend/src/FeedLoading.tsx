// What the feed shows while it loads: a live status line, the listener's interests lighting up one by one as the
// feed is matched to them, and placeholder cards in the shape of the stories to come. The page is visibly working
// instead of sitting on one line of text, and the cards land where the placeholders were.
import type { CSSProperties } from 'react'
import { useEffect, useState } from 'react'

const cards = 6

export function FeedLoading({ title, interests, slowNote }: {
  title: string
  interests: string[]
  // Shown once the wait passes ten seconds, so a slow ranking says so instead of looking stuck.
  slowNote?: string
}) {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    const timer = window.setInterval(() => setSeconds(count => count + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const active = interests.length ? seconds % interests.length : -1
  return <div className="feed-loading">
    <div className="feed-loading-status" role="status">
      <span className="feed-loading-bars" aria-hidden="true"><span /><span /><span /><span /></span>
      <div className="feed-loading-text">
        <p className="feed-loading-title">{title}</p>
        {interests.length > 0 && <p className="feed-loading-interests">
          <span className="feed-loading-label">Matching</span>
          {interests.map((name, index) => <span key={name} className="feed-loading-chip" data-active={index === active || undefined}>{name}</span>)}
        </p>}
        {slowNote && seconds >= 10 && <p className="feed-loading-note">{slowNote}</p>}
      </div>
      <span className="feed-loading-progress" aria-hidden="true" />
    </div>
    <ul className="story-grid feed-skeleton" aria-hidden="true">
      {Array.from({ length: cards }, (_, index) => <li key={index} style={{ '--i': index } as CSSProperties}>
        <div className="glass-card skeleton-card">
          <div className="skeleton-media" />
          <div className="skeleton-body">
            <span data-size="topic" /><span data-size="title" /><span data-size="title-end" />
            <span data-size="line" /><span data-size="line-end" />
          </div>
        </div>
      </li>)}
    </ul>
  </div>
}
