import type { Preferences } from './briefings'
import { InterestFields, interestOptions } from './InterestFields'
import { ThemeSwitch } from './ThemeSwitch'
import { VoiceAuditions } from './VoiceAuditions'

type Settings = { preferences: Preferences; recorded: boolean }

export function loadSettings(owner: string): Settings | null {
  try {
    const saved = JSON.parse(localStorage.getItem('tio-profile-' + owner) ?? 'null')
    const prefs = saved?.preferences
    if (typeof prefs?.profile === 'string' && prefs.profile.trim() && prefs.profile.length <= 3000
      && ['panel', 'presenter', 'live', 'realtime'].includes(prefs.mode) && Number.isInteger(prefs.minutes)
      && prefs.minutes >= 1 && prefs.minutes <= 15 && typeof saved.recorded === 'boolean') {
      return { preferences: { profile: prefs.profile, mode: prefs.mode, minutes: prefs.minutes }, recorded: saved.recorded }
    }
  } catch { /* Use default preferences when saved settings are unavailable. */ }
  return null
}

export type ListenerChoices = { topics: string[]; otherInterests: string; voiceIds: string[] }

export function listenerChoicesKey(owner: string) {
  return 'tio-listener-choices-' + owner
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? [...new Set(value.filter((item): item is string => typeof item === 'string' && !!item.trim()))] : []
}

export function loadListenerChoices(owner: string, onboarding: unknown): ListenerChoices {
  try {
    const saved = JSON.parse(localStorage.getItem(listenerChoicesKey(owner)) ?? 'null')
    if (Array.isArray(saved?.topics) && typeof saved.otherInterests === 'string' && Array.isArray(saved.voiceIds)) {
      return { topics: strings(saved.topics), otherInterests: saved.otherInterests, voiceIds: strings(saved.voiceIds) }
    }
  } catch { /* Use signup preferences when browser storage is unavailable. */ }
  const signup = onboarding as { interests_text?: unknown; preferred_persona_ids?: unknown } | undefined
  const interests = typeof signup?.interests_text === 'string' ? signup.interests_text.split(',').map(topic => topic.trim()) : []
  return {
    topics: interestOptions.filter(topic => interests.includes(topic)),
    otherInterests: interests.filter(topic => topic && !interestOptions.includes(topic)).join(', '),
    voiceIds: strings(signup?.preferred_persona_ids),
  }
}

export function Profile({ choices, storageError, onChange, briefingOpen }: {
  choices: ListenerChoices
  storageError: boolean
  onChange: (choices: ListenerChoices) => void
  briefingOpen: boolean
}) {
  return <>
    <div className="page-title"><h1>Profile</h1></div>
    <section className="profile-appearance" aria-labelledby="appearance-title"><h2 id="appearance-title">Appearance</h2><ThemeSwitch /></section>
    <div className="profile-settings">
      {storageError && <p role="status" className="muted">Changes apply for this visit. Your browser couldn’t save them.</p>}
      <InterestFields topics={choices.topics} otherInterests={choices.otherInterests}
        onTopicToggle={topic => onChange({ ...choices, topics: choices.topics.includes(topic)
          ? choices.topics.filter(value => value !== topic) : [...choices.topics, topic] })}
        onOtherChange={otherInterests => onChange({ ...choices, otherInterests })} />
      <VoiceAuditions selectedIds={choices.voiceIds} suspended={briefingOpen}
        onToggle={id => onChange({ ...choices, voiceIds: choices.voiceIds.includes(id)
          ? choices.voiceIds.filter(value => value !== id) : [...choices.voiceIds, id] })} />
    </div>
  </>
}
