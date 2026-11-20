import { useState } from 'react'
import type { FormEvent } from 'react'
import { DiscussionPreview } from './DiscussionPreview'
import { LiveHostPreview } from './LiveHostPreview'

const sampleHeadlines = [
  { topic: 'Technology', title: 'The infrastructure behind the AI boom', summary: 'Why chips, power and data centers are shaping where the next wave of investment goes.' },
  { topic: 'Economy', title: 'What central banks watch before cutting rates', summary: 'A closer look at wages, services prices and the signals that move policy.' },
  { topic: 'Clean energy', title: 'Battery storage moves closer to the center of the grid', summary: 'How utilities use storage to meet evening demand and make better use of renewable power.' },
  { topic: 'Global trade', title: 'Why shipping routes matter to the price of everyday goods', summary: 'The link between freight costs, inventory decisions and the prices customers see.' },
  { topic: 'Health & biotech', title: 'Where new drug development is getting faster', summary: 'How researchers are using better early screening to decide which treatments to pursue.' },
  { topic: 'Personal finance', title: 'What to watch when savings rates start to fall', summary: 'How changes in benchmark rates can affect savings accounts and fixed-term deposits.' },
]

const briefingSections = [
  { label: 'Top story', title: 'What to watch in the next round of central bank decisions' },
  { label: 'Interest spotlight', title: 'Technology · The infrastructure behind the AI boom' },
]

export function NewsWorkspace() {
  const [view, setView] = useState<'feed' | 'discussion' | 'voice'>('feed')
  const [showBriefing, setShowBriefing] = useState(false)
  const [question, setQuestion] = useState('')
  const [submittedQuestion, setSubmittedQuestion] = useState('')

  function askAboutNews(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const prompt = question.trim()
    if (!prompt) return
    setSubmittedQuestion(prompt)
    setQuestion('')
  }

  return (
    <div className="grid h-dvh grid-rows-[auto_minmax(0,1fr)] bg-[#faf9f6] text-[#242523] md:grid-cols-[13rem_minmax(0,1fr)] md:grid-rows-1">
      <aside className="flex items-center justify-between gap-4 border-b border-[#e6e4de] bg-[#f2f1ec] px-4 py-3 md:flex-col md:items-stretch md:justify-start md:border-r md:border-b-0 md:px-4 md:py-6">
        <span className="flex items-center gap-2.5 text-sm font-semibold">
          <span aria-hidden="true" className="grid size-8 place-items-center rounded-lg bg-[#263a33] text-sm text-white">T</span>
          TIO
        </span>
        <nav aria-label="Workspace" className="flex flex-wrap justify-end gap-1 text-sm md:mt-5 md:flex-col">
          <button type="button" aria-current={view === 'feed' ? 'page' : undefined} onClick={() => setView('feed')} className="rounded-md px-3 py-2 text-left hover:bg-[#eae8e1] aria-current:bg-[#e4e8e1] aria-current:font-medium">News feed</button>
          <button type="button" aria-current={view === 'discussion' ? 'page' : undefined} onClick={() => setView('discussion')} className="rounded-md px-3 py-2 text-left hover:bg-[#eae8e1] aria-current:bg-[#e4e8e1] aria-current:font-medium">Discussion</button>
          <button type="button" aria-current={view === 'voice' ? 'page' : undefined} onClick={() => setView('voice')} className="rounded-md px-3 py-2 text-left hover:bg-[#eae8e1] aria-current:bg-[#e4e8e1] aria-current:font-medium">Live host</button>
        </nav>
      </aside>

      <section aria-label="News workspace" className="flex min-h-0 min-w-0 flex-col">
        {view === 'feed' ? (
          <>
            <header className="border-b border-[#e6e4de] px-5 py-5 sm:px-8">
              <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-3">
                <div>
                  <h1 className="text-2xl font-semibold tracking-tight">For you</h1>
                  <p className="mt-1 text-sm text-[#605f58]">Sample headlines for your personalized feed.</p>
                </div>
                <button
                  type="button"
                  aria-expanded={showBriefing}
                  aria-controls="general-briefing"
                  onClick={() => setShowBriefing(!showBriefing)}
                  className="rounded-lg border border-[#d8d8ce] bg-white px-3.5 py-2 text-sm font-medium hover:bg-[#eef0eb]"
                >
                  General briefing
                </button>
              </div>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-8">
              <div className="mx-auto max-w-3xl">
                <section id="general-briefing" hidden={!showBriefing} className="mb-4 rounded-xl border border-[#dde3d8] bg-[#eef1eb] p-4">
                  <h2 className="font-semibold">General briefing preview</h2>
                  <dl className="mt-3 space-y-3">
                    {briefingSections.map(({ label, title }) => (
                      <div key={label}>
                        <dt className="text-xs font-medium text-[#4e6255]">{label}</dt>
                        <dd className="mt-1 text-sm">{title}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
                <ul className="divide-y divide-[#e6e4de]">
                  {sampleHeadlines.map(({ topic, title, summary }) => (
                    <li key={title} className="py-5 first:pt-1">
                      <article>
                        <p className="text-xs font-medium text-[#4e6255]">{topic}</p>
                        <h2 className="mt-1 text-lg leading-snug font-semibold tracking-tight">{title}</h2>
                        <p className="mt-1.5 text-sm leading-relaxed text-[#605f58]">{summary}</p>
                      </article>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <footer className="border-t border-[#e6e4de] px-5 py-4 sm:px-8">
              <div className="mx-auto max-w-3xl">
                {submittedQuestion && (
                  <div role="status" className="mb-3 rounded-lg bg-[#eef1eb] px-4 py-2 text-sm">
                    <p className="truncate font-medium">{submittedQuestion}</p>
                    <p className="mt-1 text-[#605f58]">Article search is a preview. It hasn’t searched the dataset.</p>
                  </div>
                )}
                <form onSubmit={askAboutNews} className="flex items-end gap-2 rounded-xl border border-[#d8d8ce] bg-white p-2.5 shadow-sm focus-within:border-[#647d6d]">
                  <label htmlFor="article-question" className="sr-only">Ask about the articles</label>
                  <textarea
                    id="article-question"
                    value={question}
                    onChange={event => setQuestion(event.target.value)}
                    placeholder="Ask for an article about a topic…"
                    rows={2}
                    maxLength={500}
                    className="min-w-0 flex-1 resize-none rounded-md px-2 py-1.5 text-sm outline-none"
                  />
                  <button type="submit" disabled={!question.trim()} className="rounded-lg bg-[#263a33] px-3.5 py-2 text-sm font-medium text-white hover:bg-[#354c42] disabled:opacity-45">Ask</button>
                </form>
              </div>
            </footer>
          </>
        ) : view === 'discussion' ? (
          <DiscussionPreview />
        ) : (
          <div className="min-h-0 overflow-y-auto px-5 py-5 sm:px-8">
            <div className="mx-auto max-w-3xl"><LiveHostPreview /></div>
          </div>
        )}
      </section>
    </div>
  )
}
