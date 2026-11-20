import { useState } from 'react'

const sampleTurns = [
  {
    speaker: 'Host',
    text: 'The European Central Bank aims for inflation of 2% over the medium term. What does that mean for the way it sets interest rates?',
    fact: 'The ECB sets its price stability objective at 2% inflation over the medium term.',
    source: { title: 'Two per cent inflation target', publisher: 'European Central Bank', url: 'https://www.ecb.europa.eu/mopo/strategy/pricestab/html/index.en.html' },
  },
  {
    speaker: 'Economy analyst',
    text: 'Changes to the ECB’s key rates influence the cost of borrowing for households and businesses. It takes time for those changes to affect inflation.',
    fact: 'Policy rate changes affect bank lending rates and demand, with a lag before they affect inflation.',
    source: { title: 'Ask us · Monetary policy', publisher: 'European Central Bank', url: 'https://www.ecb.europa.eu/ecb-and-you/ask_us/html/index.en.html' },
  },
]

export function DiscussionPreview() {
  const [activeTurn, setActiveTurn] = useState(0)
  const [userTurn, setUserTurn] = useState(false)
  const { fact, source, speaker } = sampleTurns[activeTurn]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-b border-[#e6e4de] px-5 py-5 sm:px-8">
        <div className="mx-auto max-w-5xl">
          <h1 className="text-2xl font-semibold tracking-tight">Discussion</h1>
          <p className="mt-1 text-sm text-[#605f58]">Sample discussion · How the ECB responds to inflation</p>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-8">
        <div className="mx-auto grid max-w-5xl items-start gap-6 lg:grid-cols-[minmax(0,1fr)_19rem]">
          <section aria-labelledby="transcript-title">
            <h2 id="transcript-title" className="mb-3 text-sm font-semibold">Transcript</h2>
            <ol className="space-y-3">
              {sampleTurns.map((turn, index) => (
                <li key={turn.speaker}>
                  <button
                    type="button"
                    aria-pressed={index === activeTurn}
                    onClick={() => setActiveTurn(index)}
                    className="block w-full rounded-xl border border-[#e6e4de] bg-white p-4 text-left hover:border-[#849787] aria-pressed:border-[#849787] aria-pressed:bg-[#eef1eb]"
                  >
                    <span className="block text-xs font-semibold text-[#4e6255]">{turn.speaker}</span>
                    <span className="mt-2 block text-sm leading-relaxed">{turn.text}</span>
                  </button>
                </li>
              ))}
            </ol>
          </section>

          <aside aria-labelledby="fact-check-title" className="rounded-xl border border-[#e6e4de] bg-white p-4 lg:sticky lg:top-0">
            <h2 id="fact-check-title" className="text-sm font-semibold">Fact check</h2>
            <div aria-live="polite">
              <p className="mt-3 text-xs font-medium text-[#4e6255]">{speaker} · Supporting evidence</p>
              <p className="mt-2 text-sm leading-relaxed">{fact}</p>
              <div className="mt-4 border-t border-[#e6e4de] pt-3">
                <p className="text-xs text-[#605f58]">{source.publisher}</p>
                <a href={source.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-sm font-medium text-[#263a33] underline underline-offset-3">
                  {source.title} <span aria-hidden="true">↗</span>
                </a>
              </div>
            </div>
          </aside>
        </div>
      </div>

      <footer className="border-t border-[#e6e4de] px-5 py-4 sm:px-8">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3">
          <div role="status">
            <p className="text-sm font-medium">{userTurn ? 'Your turn' : 'Discussion preview'}</p>
            <p className="mt-1 text-xs text-[#605f58]">{userTurn ? 'The hosts are paused in this preview. Microphone is off.' : 'Select a speaker turn to see its source.'}</p>
          </div>
          <button
            type="button"
            onClick={() => setUserTurn(!userTurn)}
            className="rounded-lg bg-[#263a33] px-4 py-2 text-sm font-medium text-white hover:bg-[#354c42]"
          >
            {userTurn ? 'Resume discussion' : 'Cut in'}
          </button>
        </div>
      </footer>
    </div>
  )
}
