import fixtures from '../../backend/discussion/sample.json'
import script from './recorded.json'

export type Headline = { id: string; title: string; topic: string; summary: string; thumb_url?: string | null }
export type Card = { id: string; publisher: string; language: string; url: string | null; quote: string; claim: string }
export type Segment = { id: string; story: string; story_id?: string; speaker: string; host: string; guests: string[]; text: string; sources: Card[]; audio?: string; audioUrl?: string }
export type Entry = Segment | { id: string; speaker: 'Listener'; text: string }
// pictures: each story's thumbnail by its title, for the saved briefing's card (older briefings have none).
export type Briefing = { id: string; date: string; titles: string[]; entries: Entry[]; sample: boolean; pictures?: Record<string, string> }

// The stories' web thumbnails by title, kept with a saved briefing.
export function storyPictures(stories: Headline[]): Record<string, string> {
  return Object.fromEntries(stories.flatMap(story =>
    typeof story.thumb_url === 'string' && /^https?:\/\//i.test(story.thumb_url) ? [[story.title, story.thumb_url]] : []))
}
// 'realtime' runs the panel as separate speech-to-speech agents (experimental).
// 'live' is the experimental live host: one host presents the whole briefing over a speech-to-speech session.
export type Preferences = { profile: string; minutes: number }
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
      ...line, id: line.file, host: 'Main Host', guests, story: story.title, story_id: story.id, audioUrl: '/voices/' + line.file + '.wav',
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

export function factChecksAt(segment: Segment, fraction: number): Card[] {
  // shortcut: script position approximates speech timing, use word timestamps when TTS returns alignment.
  const position = segment.text.length * Math.max(0, Math.min(1, fraction))
  const text = segment.text.toLowerCase()
  const cues = segment.sources.map(card => ({ card, at: text.indexOf(card.claim.toLowerCase()) }))
    .filter(cue => cue.card.claim.trim() && cue.at >= 0 && cue.at + cue.card.claim.length <= position)
  const latest = Math.max(-1, ...cues.map(cue => cue.at))
  return cues.filter(cue => cue.at === latest).map(cue => cue.card)
}

const FILLER = new Set(['the', 'and', 'for', 'that', 'this', 'with', 'from', 'was', 'were', 'are', 'has', 'had', 'have',
  'its', 'his', 'her', 'their', 'said', 'says', 'say', 'who', 'what', 'which', 'but', 'not', 'than', 'into', 'about'])

// Realtime speakers keep every figure as written but may reword around it, so a claim counts as heard when it was
// said outright, or when every figure and currency sign in it was said along with at least half of its content words
// (the backend's spoken_claim rule).
export function heardFactChecks(cards: Card[], transcript: string): Card[] {
  const tokens = (text: string): string[] => text.normalize('NFKC').toLowerCase().replace(/%/g, ' percent ')
    .match(/[+−-]?\p{N}+(?:[.,]\p{N}+)*|\p{L}+|\p{Sc}/gu) ?? []
  const said = tokens(transcript)
  const heard = new Set(said), phrase = ` ${said.join(' ')} `
  return cards.filter(card => {
    const claim = tokens(card.claim)
    if (!claim.length) return false
    if (phrase.includes(` ${claim.join(' ')} `)) return true
    const words = claim.filter((token, index) => /^\p{L}{3,}$/u.test(token) && !FILLER.has(token) && claim.indexOf(token) === index)
    if (words.length < 3 || !claim.filter(token => /[\p{N}\p{Sc}]/u.test(token)).every(figure => heard.has(figure))) return false
    return words.filter(word => heard.has(word)).length * 2 >= words.length
  })
}

const transcriptKey = (text: string) => text.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase()

export function savedBriefings(rows: Briefing[]): Briefing[] {
  const conversations = new Set<string>()
  return rows.slice().sort((a, b) => b.date.localeCompare(a.date)).flatMap(row => {
    const turns = new Set<string>()
    const entries = row.entries.flatMap<Entry>(entry => {
      const key = transcriptKey(entry.text)
      if (!key || key === '...' || turns.has(key)) return []
      turns.add(key)
      if ('sources' in entry) {
        const { audio, audioUrl, ...transcript } = entry
        return [transcript]
      }
      return [entry]
    })
    const key = JSON.stringify(entries.map(entry => transcriptKey(entry.text)))
    if (!entries.length || conversations.has(key)) return []
    conversations.add(key)
    return [{ ...row, titles: [...new Set(row.titles)], entries }]
  })
}

// Separate databases keep demo and signed-in listeners' device histories independent.
export function historyStore(owner: string, change?: Briefing | string): Promise<Briefing[]> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('tio-briefings-v1-' + owner, 1)
    open.onupgradeneeded = () => open.result.createObjectStore('sessions', { keyPath: 'id' })
    open.onerror = () => reject(open.error)
    open.onsuccess = () => {
      const db = open.result
      const transaction = db.transaction('sessions', 'readwrite')
      const sessions = transaction.objectStore('sessions')
      if (typeof change === 'string') sessions.delete(change)
      else if (change) sessions.put(change)
      const read = sessions.getAll()
      let saved: Briefing[] = []
      read.onsuccess = () => {
        saved = savedBriefings(read.result)
        const ids = new Set(saved.map(row => row.id))
        for (const row of read.result as Briefing[]) if (!ids.has(row.id)) sessions.delete(row.id)
        for (const row of saved) sessions.put(row)
      }
      transaction.oncomplete = () => { db.close(); resolve(saved) }
      transaction.onabort = () => { db.close(); reject(transaction.error) }
    }
  })
}
