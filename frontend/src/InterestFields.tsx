import { useId, useState } from 'react'
import { InterestRadar, maxLevel } from './InterestRadar'
import type { InterestLevels, RadarTopic } from './InterestRadar'

export type { InterestLevels } from './InterestRadar'

// Short names label the radar's spokes; the full name is what is stored and read out.
const topics: RadarTopic[] = [
  { name: 'Stock markets', short: 'Stocks' }, { name: 'Technology', short: 'Tech' },
  { name: 'Central banks and inflation', short: 'Inflation' }, { name: 'Currencies', short: 'Currencies' },
  { name: 'Energy and commodities', short: 'Energy' }, { name: 'Company earnings', short: 'Earnings' },
  { name: 'Artificial intelligence', short: 'AI' }, { name: 'Climate and clean energy', short: 'Climate' },
  { name: 'Science and space', short: 'Science' }, { name: 'Health and biotech', short: 'Health' },
  { name: 'Housing and real estate', short: 'Housing' }, { name: 'Jobs and the economy', short: 'Economy' },
  { name: 'Personal finance', short: 'Your money' }, { name: 'Startups and entrepreneurship', short: 'Startups' },
  { name: 'Global trade', short: 'Trade' }, { name: 'Politics and geopolitics', short: 'Politics' },
  { name: 'Food and agriculture', short: 'Food' }, { name: 'Transport and mobility', short: 'Transport' },
  { name: 'Media and entertainment', short: 'Media' }, { name: 'Sports business', short: 'Sports' },
]

export const interestOptions = topics.map(topic => topic.name)

/** A radar topic's short name ('Central banks and inflation' -> 'Inflation'); anything else comes back unchanged. */
export const shortTopic = (name: string) => topics.find(topic => topic.name === name)?.short ?? name

/** Known topics with a whole level from 1 to 3, in the radar's order. */
export function readLevels(value: unknown): InterestLevels {
  const levels: InterestLevels = {}
  if (!value || typeof value !== 'object' || Array.isArray(value)) return levels
  for (const topic of interestOptions) {
    const level = (value as Record<string, unknown>)[topic]
    if (Number.isInteger(level) && (level as number) >= 1 && (level as number) <= maxLevel) levels[topic] = level as number
  }
  return levels
}

/** The picked topics, strongest first. Ties keep the radar's order. */
export function rankedTopics(levels: InterestLevels) {
  return interestOptions.filter(topic => levels[topic]).sort((a, b) => levels[b] - levels[a])
}

/** The recommender's interest list (profiles.interests_text): strongest topics first, then the listener's own words. */
export function interestsText(levels: InterestLevels, otherInterests: string) {
  return [...rankedTopics(levels), otherInterests.trim()].filter(Boolean).join(', ')
}

/** Radar levels and own words from a saved profile. Profiles from before the radar list topics without levels:
 * each listed topic starts at "Interested". */
export function interestsFromProfile(levels: unknown, text: unknown) {
  const parts = typeof text === 'string' ? text.split(',').map(part => part.trim()).filter(Boolean) : []
  let picked = readLevels(levels)
  if (!Object.keys(picked).length) picked = Object.fromEntries(interestOptions.filter(topic => parts.includes(topic)).map(topic => [topic, 2]))
  return { levels: picked, otherInterests: parts.filter(part => !interestOptions.includes(part)).join(', ') }
}

type Props = {
  levels?: InterestLevels
  otherInterests?: string
  onLevelsChange?: (levels: InterestLevels) => void
  onOtherChange?: (value: string) => void
}

/** Without `levels` (sign-up) the radar keeps its own state and posts it with the form: `interests` lists the picked
 * topics strongest first and `interest_levels` holds the radar as JSON. */
export function InterestFields({ levels, otherInterests, onLevelsChange, onOtherChange }: Props) {
  const legendId = useId()
  const [own, setOwn] = useState<InterestLevels>({})
  const value = levels ?? own
  const change = onLevelsChange ?? setOwn
  const picked = rankedTopics(value)

  return <fieldset className="interest-options">
    <legend id={legendId} className="mb-2 text-sm font-medium">Which topics interest you?</legend>
    <InterestRadar topics={topics} levels={value} onChange={change} labelledBy={legendId} />
    {picked.map(topic => <input key={topic} type="hidden" name="interests" value={topic} />)}
    <input type="hidden" name="interest_levels" value={JSON.stringify(value)} />
    <label className="block text-sm font-medium">
      Other interests (optional)
      <input name="other_interests" type="text" maxLength={300} className="form-field" value={otherInterests}
        onChange={onOtherChange && (event => onOtherChange(event.target.value))} />
    </label>
  </fieldset>
}
