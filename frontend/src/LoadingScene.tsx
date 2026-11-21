// What the listener watches while the briefing is prepared: their headlines drift in one at a time and are taken
// into the host's ring, and a checklist below it follows the real preparation steps as they finish.
import { useEffect, useState } from 'react'
import type { Headline } from './briefings'

export type PrepStep = { key: string; label: string; detail?: string }

export function LoadingScene({ stories, steps, current }: { stories: Headline[]; steps: PrepStep[]; current: string }) {
  const [shown, setShown] = useState(0)
  const reading = stories.filter(story => story.id !== 'planning')
  const at = steps.findIndex(step => step.key === current)

  useEffect(() => {
    const timer = window.setInterval(() => setShown(count => count + 1), 3000)
    return () => window.clearInterval(timer)
  }, [])

  const story = reading.length ? reading[shown % reading.length] : null
  return <>
    <div className="loading-scene" aria-hidden="true">
      {story && <div key={shown} className="loading-card" data-side={shown % 2 ? 'right' : 'left'}>
        {story.thumb_url && <img src={story.thumb_url} alt="" referrerPolicy="no-referrer" onError={event => event.currentTarget.remove()} />}
        <span><small>{story.topic || 'Headline'}</small><strong>{story.title}</strong></span>
      </div>}
      {story && <span key={'gulp' + shown} className="loading-gulp" />}
    </div>
    <ol className="loading-steps" aria-label="Preparing your briefing">
      {steps.map((step, index) => {
        const state = index < at ? 'done' : index === at ? 'active' : 'next'
        return <li key={step.key} data-state={state}>
          <span className="loading-mark" aria-hidden="true" />
          <span>{step.label}{step.detail && <small>{step.detail}</small>}</span>
          <span className="sr-only">{state === 'done' ? ', done' : state === 'active' ? ', in progress' : ''}</span>
        </li>
      })}
    </ol>
  </>
}
