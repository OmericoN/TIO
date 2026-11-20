import fixtures from '../../backend/discussion/sample.json'
import script from './recorded.json'

export type Headline = { id: string; title: string; topic: string; summary: string; image?: string }
export type Card = { id: string; publisher: string; language: string; url: string | null; quote: string; claim: string }
export type Segment = { id: string; story: string; speaker: string; host: string; guests: string[]; text: string; sources: Card[]; audio?: string; audioUrl?: string }
export type Entry = Segment | { id: string; speaker: 'Listener'; text: string }
export type Briefing = { id: string; date: string; titles: string[]; entries: Entry[]; sample: boolean }
export type Preferences = { profile: string; mode: 'panel' | 'presenter'; minutes: number }
export type DiscussionPlan = {
  segments: { article_ids: string[]; estimated_seconds: number }[]
  hosts: string[]
  tones: Record<string, string>
}
export type PreparedBriefing = { stories: Headline[]; preferences: Preferences; plan: DiscussionPlan; listenerId?: string }

export function sampleArticle(id: string) {
  return fixtures.flatMap(story => story.articles).find(article => article.id === id)
}

export const sampleHeadlines: Headline[] = fixtures.map(s => ({
  id: s.id, title: s.headline, summary: s.articles[0].body,
  topic: s.id === 'sample-rates' ? 'Monetary policy' : 'Company earnings',
  image: s.id === 'sample-rates' ? 'economy' : 'finance',
}))

const claims: Record<string, [number, string][]> = {
  'revenue-open': [[0, 'Aster Works reports quarterly revenue growth.']],
  'revenue-markets': [[0, 'Sample Markets puts growth at eight percent for continuing operations.'], [1, 'The German report gives six percent across all operations.']],
  'revenue-economy': [[2, 'For all operations, the Dutch report estimates seven percent'], [1, 'the German report gives six']],
  'rates-open': [[0, 'the Aster Reserve decision']],
  'rates-economy': [[0, 'The reserve held its policy rate at three percent.'], [1, 'Both the English and Dutch reports give the same rate.']],
}

export function recordedTurns(stories: Headline[]): Segment[] {
  return stories.flatMap(story => {
    const source = fixtures.find(s => s.id === story.id)!
    const lines = script[source.id as keyof typeof script]
    const guests = [...new Set(lines.map(l => l.speaker).filter(s => s !== 'Main Host'))]
    return lines.map(line => ({
      ...line, id: line.file, host: 'Main Host', guests, story: story.title, audioUrl: '/voices/' + line.file + '.wav',
      sources: (claims[line.file] ?? []).map(([index, claim]) => {
        const a = source.articles[index]
        return { id: a.id, language: a.language, url: a.url, publisher: a.publisher, claim, quote: a.body }
      }),
    }))
  })
}

export function audioUrl(segment: Segment) {
  return segment.audioUrl ?? 'data:audio/mpeg;base64,' + segment.audio
}

// Separate databases keep demo and signed-in listeners' device histories independent.
export function historyStore(owner: string, change?: Briefing | string): Promise<Briefing[]> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('tio-briefings-v1-' + owner, 1)
    open.onupgradeneeded = () => open.result.createObjectStore('sessions', { keyPath: 'id' })
    open.onerror = () => reject(open.error)
    open.onsuccess = () => {
      const db = open.result
      const transaction = db.transaction('sessions', change === undefined ? 'readonly' : 'readwrite')
      const sessions = transaction.objectStore('sessions')
      const read = change === undefined ? sessions.getAll() : null
      if (typeof change === 'string') sessions.delete(change)
      else if (change) sessions.put(change)
      transaction.oncomplete = () => { db.close(); resolve(read?.result ?? []) }
      transaction.onabort = () => { db.close(); reject(transaction.error) }
    }
  })
}
