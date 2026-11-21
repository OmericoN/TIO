import { useState } from 'react'
import type { Preferences } from './briefings'
import { ThemeSwitch } from './ThemeSwitch'

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

type Props = {
  preferences: Preferences
  recorded: boolean
  sample: boolean
  online: boolean
  onSave: (settings: Settings) => string
}

export function Profile({ preferences, recorded, sample, online, onSave }: Props) {
  const [draft, setDraft] = useState(preferences)
  const [playSample, setPlaySample] = useState(recorded)
  const [status, setStatus] = useState('')
  return <>
    <div className="page-title"><h1>Profile</h1><span className="edition-note">On this device</span></div>
    <form className="profile-settings" onSubmit={event => {
      event.preventDefault()
      if (!draft.profile.trim()) { setStatus('Enter a listener profile before saving.'); return }
      setStatus(onSave({ preferences: { ...draft, profile: draft.profile.trim() }, recorded: sample && playSample }))
    }} onChange={() => setStatus('')}>
      <fieldset><legend>Your listening profile</legend>
        <p className="muted">Tell the host about your interests, goals, and the depth you prefer.</p>
        <label className="field">Listener profile<textarea value={draft.profile} rows={4} required maxLength={3000}
          onChange={e => setDraft({ ...draft, profile: e.target.value })} /></label>
      </fieldset>
      <fieldset><legend>Briefing settings</legend>
        {sample && <label className="field">Playback<select disabled={!online} value={playSample ? 'sample' : 'live'} onChange={e => setPlaySample(e.target.value === 'sample')}>
          <option value="sample">Recorded sample</option><option value="live">Generate a personal briefing</option>
        </select></label>}
        {!online && <p className="muted">Recorded samples are available. Start the backend to generate a personal briefing.</p>}
        <div className="format-fields">
          <label className="field">Format<select value={draft.mode} onChange={e => setDraft({ ...draft, mode: e.target.value as Preferences['mode'] })}><option value="panel">Expert panel</option><option value="presenter">Presenter</option><option value="live">Live host (experimental)</option><option value="realtime">Realtime panel (experimental)</option></select></label>
          <label className="field">Minutes<input type="number" min="1" max="15" step="1" required value={draft.minutes} onChange={e => setDraft({ ...draft, minutes: Number(e.target.value) })} /></label>
        </div>
        {sample && playSample && <p className="muted">The recorded sample uses its original script and length. Your profile, format, and duration apply to generated briefings.</p>}
      </fieldset>
      <div className="profile-save"><button className="button" type="submit">Save preferences</button><p role="status">{status}</p></div>
    </form>
    <section className="profile-appearance" aria-labelledby="appearance-title"><h2 id="appearance-title">Appearance</h2><ThemeSwitch /></section>
  </>
}
