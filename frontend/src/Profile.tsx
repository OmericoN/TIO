import type { Preferences } from './briefings'
import { InterestFields, interestsFromProfile, readLevels } from './InterestFields'
import type { InterestLevels } from './InterestFields'
import { ThemeSwitch } from './ThemeSwitch'
import { VoiceAuditions } from './VoiceAuditions'

type Settings = { preferences: Preferences; recorded: boolean }

export function loadSettings(owner: string): Settings | null {
  try {
    const saved = JSON.parse(localStorage.getItem('tio-profile-' + owner) ?? 'null')
    const prefs = saved?.preferences
    if (typeof prefs?.profile === 'string' && prefs.profile.trim() && prefs.profile.length <= 3000
      && Number.isInteger(prefs.minutes)
      && prefs.minutes >= 1 && prefs.minutes <= 15 && typeof saved.recorded === 'boolean') {
      return { preferences: { profile: prefs.profile, minutes: prefs.minutes }, recorded: saved.recorded }
    }
  } catch { /* Use default preferences when saved settings are unavailable. */ }
  return null
}

export type ListenerChoices = { levels: InterestLevels; otherInterests: string; voiceIds: string[] }

export function listenerChoicesKey(owner: string) {
  return 'tio-listener-choices-' + owner
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? [...new Set(value.filter((item): item is string => typeof item === 'string' && !!item.trim()))] : []
}

export function loadListenerChoices(owner: string, onboarding: unknown): ListenerChoices {
  try {
    const saved = JSON.parse(localStorage.getItem(listenerChoicesKey(owner)) ?? 'null')
    if (typeof saved?.otherInterests === 'string' && Array.isArray(saved.voiceIds)) {
      // Choices saved before the radar list checked topics; each starts at "Interested".
      const levels = Array.isArray(saved.topics) ? readLevels(Object.fromEntries(strings(saved.topics).map(topic => [topic, 2])))
        : readLevels(saved.levels)
      return { levels, otherInterests: saved.otherInterests, voiceIds: strings(saved.voiceIds) }
    }
  } catch { /* Use signup preferences when browser storage is unavailable. */ }
  const signup = onboarding as { interests_text?: unknown; interest_levels?: unknown; preferred_persona_ids?: unknown } | undefined
  return { ...interestsFromProfile(signup?.interest_levels, signup?.interests_text), voiceIds: strings(signup?.preferred_persona_ids) }
}

export function Profile({ choices, storageError, syncNotice, onChange, briefingOpen, email, onSignOut }: {
  choices: ListenerChoices
  storageError: boolean
  syncNotice?: string
  email?: string
  onSignOut?: () => void
  onChange: (choices: ListenerChoices) => void
  briefingOpen: boolean
}) {
  return <>
    <div className="page-title"><h1>Profile</h1></div>
    <section className="profile-appearance" aria-labelledby="appearance-title"><h2 id="appearance-title">Appearance</h2><ThemeSwitch /></section>
    <div className="profile-settings">
      {storageError && <p role="status" className="muted">Changes apply for this visit. Your browser couldn’t save them.</p>}
      {syncNotice && <p role="status" className="muted">{syncNotice}</p>}
      <InterestFields levels={choices.levels} onLevelsChange={levels => onChange({ ...choices, levels })} />
      <VoiceAuditions selectedIds={choices.voiceIds} suspended={briefingOpen}
        onToggle={id => onChange({ ...choices, voiceIds: choices.voiceIds.includes(id)
          ? choices.voiceIds.filter(value => value !== id) : [...choices.voiceIds, id] })} />
    </div>
    {onSignOut && <section className="profile-account" aria-labelledby="account-title">
      <div><h2 id="account-title">Account</h2>{email && <p className="muted">{email}</p>}</div>
      <button type="button" className="profile-sign-out" onClick={onSignOut}>Sign out</button>
    </section>}
  </>
}
