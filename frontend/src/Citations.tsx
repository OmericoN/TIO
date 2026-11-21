// Citations shown while the panel talks: the articles behind the line being spoken, and every fact card the panel has
// drawn on so far with the articles that support it, newest first. A line's facts are listed as soon as it starts,
// and the ones the current line uses are highlighted.
// The list takes whatever height the room has left and scrolls for the rest. Clicking a claim opens its article in a
// new tab; a claim whose sources have no link opens the supporting passages instead.
import { useEffect, useState } from 'react'
import { sampleArticle } from './briefings'
import type { Card } from './briefings'

function sourceUrl(card: Card) {
  if (card.url && /^https?:\/\//i.test(card.url)) return card.url
  return sampleArticle(card.id) ? new URL(`/sample-source?id=${encodeURIComponent(card.id)}`, location.origin).href : null
}

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
    const url = sourceUrl(card)
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

// One entry per article, since several claims often cite the same one.
function articles(cards: Card[]) {
  const seen = new Map<string, Card>()
  for (const card of cards) if (!seen.has(card.id || card.publisher)) seen.set(card.id || card.publisher, card)
  return [...seen.values()]
}

// The article a claim links to: its first source that has one.
function claimArticle(cards: Card[]) {
  for (const card of cards) {
    const url = sourceUrl(card)
    if (url) return { url, publisher: card.publisher }
  }
  return null
}

function SourceLink({ card }: { card: Card }) {
  const url = sourceUrl(card)
  return url ? <a href={url} target="_blank" rel="noreferrer">{card.publisher}</a> : <span>{card.publisher}</span>
}

// The panel stays for the whole briefing: claims from earlier stories remain below the newer ones, under their story's
// name, and the top row always says what the line being spoken cites.
export function Citations({ speaking, cited, story }: { speaking: Card[]; cited: Card[]; story: string }) {
  const [claims, setClaims] = useState<{ story: string; cards: Card[] }[]>([])

  useEffect(() => {
    setClaims(current => {
      const fresh = [...new Set(cited.map(card => card.claim))]
        .filter(claim => claim.trim() && !current.some(group => group.cards[0].claim === claim))
      return fresh.length
        ? [...fresh.reverse().map(claim => ({ story, cards: cited.filter(card => card.claim === claim) })), ...current]
        : current
    })
  }, [cited])

  const outlets = articles(speaking)
  const current = new Set(speaking.map(card => card.claim))
  const labelled = new Set(claims.map(group => group.story)).size > 1
  return <section className="citations" aria-label="Citations">
    <p className="citation-outlets">
      <span className="citation-label">Sources</span>
      {outlets.length
        ? outlets.map(card => <SourceLink key={card.id || card.publisher} card={card} />)
        : <span className="citation-none">{claims.length ? 'This line cites no articles' : 'Articles appear here as the panel cites them'}</span>}
    </p>
    {claims.length === 0 && <p className="citation-none">Each fact the panel draws on is listed here, linked to its article.</p>}
    {claims.length > 0 && <ol className="citation-list" aria-label="Fact checks">
      {claims.map(({ story: from, cards }, index) => {
        const article = claimArticle(cards)
        return <li key={cards[0].claim} data-current={current.has(cards[0].claim) || undefined}>
          {labelled && from !== claims[index - 1]?.story && <p className="citation-story">{from}</p>}
          <div className="citation-item">
            {article
              ? <a className="citation-claim" href={article.url} target="_blank" rel="noreferrer"
                  title={`Read the ${article.publisher} article`}>{cards[0].claim}</a>
              : <button type="button" className="citation-claim" title="Open the supporting passages in a new tab"
                  onClick={() => openProof(cards)}>{cards[0].claim}</button>}
            <p className="citation-sources">{articles(cards).map(card => <SourceLink key={card.id || card.publisher} card={card} />)}</p>
          </div>
        </li>
      })}
    </ol>}
  </section>
}
