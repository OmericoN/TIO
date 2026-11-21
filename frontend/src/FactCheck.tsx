import { useEffect, useRef, useState } from 'react'
import { sampleArticle } from './briefings'
import type { Card } from './briefings'

const factInterval = 14_000

function openProof(cards: Card[]) {
  const page = document.implementation.createHTMLDocument('Fact-check source · TIO')
  const style = page.createElement('style')
  style.textContent = `
    :root { color-scheme: dark; background: #191e1a; color: #eeeee2; font: 17px/1.65 system-ui, sans-serif; }
    body { width: min(42rem, calc(100% - 36px)); margin: 3rem auto; overflow-wrap: anywhere; }
    h1 { font-size: clamp(23px, 5vw, 38px); line-height: 1.2; }
    section { border-top: 1px solid #465041; padding-block: 1rem; }
    h2, a { color: #dbcc88; } h2 { font-size: 1rem; }
    blockquote { margin: 1rem 0; white-space: pre-wrap; }
  `
  const viewport = page.createElement('meta')
  viewport.name = 'viewport'
  viewport.content = 'width=device-width, initial-scale=1'
  page.head.append(viewport, style)
  const title = page.createElement('h1')
  title.textContent = cards[0].claim
  page.body.append(title)
  for (const card of cards) {
    const section = page.createElement('section')
    const publisher = page.createElement('h2')
    const passage = page.createElement('blockquote')
    publisher.textContent = card.publisher
    passage.textContent = card.quote
    passage.lang = card.language
    section.append(publisher, passage)
    const url = card.url && /^https?:\/\//i.test(card.url) ? card.url
      : sampleArticle(card.id) ? new URL(`/sample-source?id=${encodeURIComponent(card.id)}`, location.origin).href : null
    if (url) {
      const source = page.createElement('a')
      source.href = url
      source.rel = 'noreferrer'
      source.textContent = 'Read the original article'
      section.append(source)
    }
    page.body.append(section)
  }
  const preview = URL.createObjectURL(new Blob(['<!doctype html>', page.documentElement.outerHTML], { type: 'text/html;charset=utf-8' }))
  window.open(preview, '_blank', 'noopener,noreferrer')
  window.setTimeout(() => URL.revokeObjectURL(preview), 60_000)
}

export function FactCheck({ cards }: { cards: Card[] }) {
  const [visible, setVisible] = useState<Card[]>([])
  const lastShown = useRef(0)
  const claim = visible[0]?.claim

  useEffect(() => {
    const next = cards[0]?.claim
    if (!next || next === claim) return

    function show() {
      lastShown.current = Date.now()
      setVisible(cards.filter(card => card.claim === next))
    }

    const remaining = lastShown.current + factInterval - Date.now()
    if (remaining <= 0) { show(); return }
    const timer = window.setTimeout(show, remaining)
    return () => window.clearTimeout(timer)
  }, [cards, claim])

  if (!claim) return null
  return <aside key={claim} className="fact-check glass-card" aria-label="Fact check">
    <blockquote className="fact-claim"><span aria-hidden="true">“</span><p>{claim}</p><span aria-hidden="true">”</span></blockquote>
    <button type="button" className="fact-info" aria-label="Open supporting passage in a new tab" onClick={() => openProof(visible)}>i</button>
  </aside>
}
