import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { NewsWorkspace } from './NewsWorkspace'
import { sampleArticle } from './briefings'
import { AudioRibbon, Brand } from './Brand'
import { VoiceAuditions } from './VoiceAuditions'
import { InterestFields, readLevels } from './InterestFields'
import { expiring, loadSession, refreshSession, revokeSession, saveSession, sessionFrom } from './session'
import type { Session } from './session'
import './styles.css'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL?.trim().replace(/\/+$/, '')
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim()
const configured = Boolean(supabaseUrl && publishableKey?.startsWith('sb_publishable_'))
const demoAvailable = import.meta.env.DEV && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)

const externalTermsUrl = import.meta.env.VITE_TERMS_URL?.trim()
const bundledTermsPath = '/terms-v1.html'
const termsUrl = externalTermsUrl?.startsWith('https://') ? externalTermsUrl : bundledTermsPath
const fieldClass = 'form-field'

function OnboardingFields({ step }: { step: number }) {
  return (
    <>
      <fieldset hidden={step !== 0} className="space-y-4">
        <legend className="pr-2 font-semibold">About you</legend>
        <label className="block text-sm font-medium">
          Display name
          <input name="display_name" autoComplete="nickname" required maxLength={80} className={fieldClass} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm font-medium">
            First name
            <input name="first_name" autoComplete="given-name" required maxLength={80} className={fieldClass} />
          </label>
          <label className="block text-sm font-medium">
            Last name
            <input name="last_name" autoComplete="family-name" required maxLength={80} className={fieldClass} />
          </label>
        </div>
        <label className="block text-sm font-medium">
          Date of birth
          <input name="date_of_birth" type="date" autoComplete="bday" className={fieldClass} />
        </label>
        <label className="block text-sm font-medium">
          Gender
          <select name="gender" className={fieldClass}>
            <option value="">Prefer not to say</option>
            <option>Woman</option>
            <option>Man</option>
            <option>Nonbinary</option>
            <option>Another identity</option>
          </select>
        </label>
      </fieldset>
      <fieldset hidden={step !== 1} className="space-y-4">
        <legend className="pr-2 font-semibold">Your briefing</legend>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm font-medium">
            Primary language
            <select name="preferred_language" defaultValue="en" className={fieldClass}>
              <option value="en">English</option>
              <option value="nl">Dutch</option>
              <option value="de">German</option>
            </select>
          </label>
          <label className="block text-sm font-medium">
            Second language
            <select name="secondary_language" className={fieldClass}>
              <option value="">None</option>
              <option value="en">English</option>
              <option value="nl">Dutch</option>
              <option value="de">German</option>
            </select>
          </label>
        </div>
        <InterestFields />
      </fieldset>
    </>
  )
}

function TermsAgreement() {
  return (
    <label className="flex items-start gap-2 border-t border-slate-200 pt-4 text-sm">
      <input type="checkbox" name="accept_terms" required className="mt-1" />
      <span>
        I agree to the{' '}
        <a href={termsUrl} target="_blank" rel="noreferrer" className="underline">Terms and Conditions</a>.
      </span>
    </label>
  )
}

function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [restoring, setRestoring] = useState(() => configured && !!loadSession())
  const sessionNow = useRef<Session | null>(null)
  const refreshing = useRef<Promise<Session | 'rejected' | 'offline'> | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [signInEmail, setSignInEmail] = useState('')
  const [creatingAccount, setCreatingAccount] = useState(false)
  const [step, setStep] = useState(0)
  const [preferredPersonaIds, setPreferredPersonaIds] = useState<string[]>([])
  const authRef = useRef<HTMLElement>(null)

  useEffect(() => {
    authRef.current?.querySelector<HTMLElement>(
      'fieldset:not([hidden]) > label > input, fieldset:not([hidden]) select:not(fieldset[hidden] select), #voice-auditions-title',
    )?.focus()
  }, [creatingAccount, step])

  const action = creatingAccount ? 'Create account' : 'Sign in'

  function enter(next: Session) {
    sessionNow.current = next
    saveSession(next)
    setSession(next)
  }

  // A session saved in this browser signs the listener back in after a reload; an expired one is renewed first.
  useEffect(() => {
    const stored = loadSession()
    if (!stored || !configured) return
    void (async () => {
      const renewed = expiring(stored) ? await refreshSession(stored) : stored
      if (renewed === 'rejected') saveSession(null)
      else enter(renewed === 'offline' ? stored : renewed)
      setRestoring(false)
    })()
  }, [])

  // A current access token for backend calls, renewed when it is about to expire. Undefined once signed out.
  const accessToken = useCallback(async () => {
    const current = sessionNow.current
    if (!current) return undefined
    if (!expiring(current)) return current.accessToken
    refreshing.current ??= refreshSession(current).finally(() => { refreshing.current = null })
    const renewed = await refreshing.current
    if (typeof renewed === 'string' || sessionNow.current !== current) return sessionNow.current?.accessToken
    enter(renewed)
    return renewed.accessToken
  }, [])

  function signOut() {
    const current = sessionNow.current
    sessionNow.current = null
    saveSession(null)
    setSession(null)
    setCreatingAccount(false)
    setStep(0)
    setPreferredPersonaIds([])
    setError('')
    setSignInEmail(current?.user.email ?? '')
    setNotice('You’re signed out.')
    if (current) void revokeSession(current)
  }

  function switchMode() {
    setCreatingAccount(!creatingAccount)
    setPreferredPersonaIds([])
    setStep(0)
    setError('')
    setNotice('')
  }

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return

    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const email = String(form.get('email') ?? '').trim()
    const password = String(form.get('password') ?? '')
    setError('')
    setNotice('')

    const text = (name: string) => String(form.get(name) ?? '').trim()
    const primaryLanguage = text('preferred_language')
    const secondaryLanguage = text('secondary_language')
    const interests = form.getAll('interests') // picked radar topics, strongest first
    const interestLevels = readLevels(JSON.parse(text('interest_levels') || '{}'))

    for (const field of formElement.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
      'input, select, textarea',
    )) {
      if (!field.closest('[hidden]') && !field.reportValidity()) return
    }

    if (creatingAccount && step === 0) {
      if (['display_name', 'first_name', 'last_name'].some(name => !text(name))) {
        setError('Enter your display name, first name, and last name.')
        return
      }
      const birthDate = text('date_of_birth')
      const now = new Date()
      if (birthDate && new Date(birthDate + 'T00:00:00') > now) {
        setError('Date of birth must be today or earlier.')
        return
      }
      setStep(1)
      return
    }

    if (creatingAccount && step === 1) {
      if (secondaryLanguage && secondaryLanguage === primaryLanguage) {
        setError('Choose a different second language or select None.')
        return
      }
      if (!interests.length) {
        setError('Set at least one topic on the radar.')
        return
      }
      setStep(2)
      return
    }

    if (creatingAccount && step === 2) {
      setStep(3)
      return
    }

    if (!configured) {
      setError('Supabase sign-in is not configured.')
      return
    }
    if (creatingAccount && Array.from(password).length < 8) {
      setError('Use at least 8 characters for your password.')
      return
    }
    if (creatingAccount && password !== form.get('password_confirmation')) {
      setError('The passwords do not match.')
      return
    }

    const data = creatingAccount ? {
      tio_onboarding: {
        display_name: text('display_name'),
        first_name: text('first_name'),
        last_name: text('last_name'),
        date_of_birth: text('date_of_birth') || null,
        gender: text('gender') || null,
        preferred_language: primaryLanguage,
        secondary_language: secondaryLanguage || null,
        // The existing signup trigger requires a goal; use the selected topics.
        listener_goal: interests.join(', '),
        interests_text: interests.join(', '),
        interest_levels: interestLevels,
        preferred_persona_ids: preferredPersonaIds,
        accepted_terms: true,
        terms_url: termsUrl,
      },
    } : undefined

    const endpoint = creatingAccount ? 'signup' : 'token?grant_type=password'
    setPending(true)

    try {
      const response = await fetch(`${supabaseUrl}/auth/v1/${endpoint}`, {
        method: 'POST',
        headers: {
          apikey: publishableKey,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password, data }),
      })

      if (!response.ok) {
        const failure = await response.json().catch(() => null)
        if (creatingAccount && ['user_already_exists', 'email_exists'].includes(failure?.error_code)) {
          setSignInEmail(email)
          setCreatingAccount(false)
          setStep(0)
          setNotice('An account already exists for this email. Sign in below.')
        } else if (failure?.error_code === 'weak_password') {
          setError('Choose a stronger password that meets the project’s password requirements.')
        } else if (failure?.error_code === 'invalid_credentials') {
          setError('Email or password is incorrect.')
        } else if (failure?.error_code === 'email_not_confirmed') {
          setError('Confirm your email before signing in.')
        } else if (response.status === 429) {
          setError('Too many attempts. Try again in a moment.')
        } else {
          setError(creatingAccount ? 'Could not create an account. Try again.' : 'Sign-in failed. Try again.')
        }
        return
      }

      const body = await response.json()
      if (creatingAccount && !body?.access_token) {
        setSignInEmail(email)
        setCreatingAccount(false)
        setStep(0)
        setNotice('If you just registered, check your inbox for a confirmation link before signing in.')
        return
      }
      const next = sessionFrom(body)
      if (!next) {
        setError('Sign-in failed. Try again.')
        return
      }
      if (data && !next.user.user_metadata?.tio_onboarding) next.user = { ...next.user, user_metadata: { ...next.user.user_metadata, ...data } }
      // Build the listener's interest embedding now, so the first recommendations don't wait for it.
      // The feed still works without it: the recommender embeds a missing profile on demand.
      await fetch('/api/profile/embed', {
        method: 'POST',
        headers: { Authorization: `Bearer ${next.accessToken}` },
        signal: AbortSignal.timeout(15000),
      }).catch(() => null)
      enter(next)
    } catch {
      setError('Could not reach Supabase. Try again.')
    } finally {
      setPending(false)
    }
  }

  if (session) return <main><NewsWorkspace key={session.user.id} owner={'account-' + session.user.id}
    onboarding={session.user.user_metadata?.tio_onboarding} email={session.user.email}
    // The development proxy serves the recommender and briefing endpoints on localhost and through the tunnel.
    listenerId={import.meta.env.DEV ? session.user.id : undefined} accessToken={accessToken} onSignOut={signOut} /></main>
  if (restoring) return <main className="auth-page">
    <header className="masthead" aria-hidden="true"><AudioRibbon /></header>
    <p role="status" className="auth-layout muted">Signing you in…</p>
  </main>

  return (
    <main className="auth-page">
      <header className="masthead" aria-hidden="true"><AudioRibbon /></header>
      <div className="auth-layout">
        <section ref={authRef} aria-labelledby="login-title" className="auth-form">
          <h1 id="login-title">{creatingAccount ? <>Create a <Brand /> account</> : <>Sign in to <Brand /></>}</h1>
          {creatingAccount && <p className="mt-2 text-sm text-slate-600">Step {step + 1} of 4</p>}
          <form key={action} onSubmit={submitAuth} noValidate className="mt-6 space-y-4">
            {creatingAccount && <OnboardingFields step={step} />}
            {creatingAccount && step === 2 && <VoiceAuditions selectedIds={preferredPersonaIds}
              onToggle={id => {
                if (!preferredPersonaIds.includes(id) && preferredPersonaIds.length >= 3) {
                  setError('You can pick up to three voices. Remove one before adding another.')
                  return
                }
                setError('')
                setPreferredPersonaIds(ids => ids.includes(id) ? ids.filter(selected => selected !== id) : [...ids, id])
              }} />}
            <fieldset hidden={creatingAccount && step !== 3} className="space-y-4">
              {creatingAccount && <legend className="pr-2 font-semibold">Your account</legend>}
              <label className="block text-sm font-medium">
                Email
                <input
                  type="email"
                  name="email"
                  defaultValue={creatingAccount ? undefined : signInEmail}
                  autoComplete="username"
                  required
                  className={fieldClass}
                />
              </label>
              <label className="block text-sm font-medium">
                Password
                <input
                  type="password"
                  name="password"
                  autoComplete={creatingAccount ? 'new-password' : 'current-password'}
                  minLength={creatingAccount ? 8 : undefined}
                  aria-describedby={creatingAccount ? 'password-hint' : undefined}
                  required
                  className={fieldClass}
                />
              </label>
              {creatingAccount && <p id="password-hint" className="text-sm text-slate-600">Use at least 8 characters.</p>}
              {creatingAccount && (
                <>
                  <label className="block text-sm font-medium">
                    Confirm password
                    <input
                      name="password_confirmation"
                      type="password"
                      autoComplete="new-password"
                      required
                      className={fieldClass}
                    />
                  </label>
                  <TermsAgreement />
                </>
              )}
            </fieldset>
            {notice && <p role="status" className="text-sm text-slate-700">{notice}</p>}
            {!configured && <p className="text-sm text-slate-600">Supabase sign-in is not configured.</p>}
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            <button
              type="submit"
              disabled={pending}
              className="button w-full"
            >
              {pending ? 'Please wait…' : creatingAccount && step < 3 ? 'Continue' : action}
            </button>
            {creatingAccount && step > 0 && (
              <button
                type="button"
                disabled={pending}
                onClick={() => {
                  setError('')
                  setNotice('')
                  setStep(step - 1)
                }}
                className="text-sm font-medium text-slate-700 underline disabled:opacity-50"
              >
                Back
              </button>
            )}
          </form>
          <button
            type="button"
            disabled={pending}
            onClick={switchMode}
            className="mt-4 text-sm font-medium text-slate-700 underline disabled:opacity-50"
          >
            {creatingAccount ? 'Back to sign in' : 'Create an account'}
          </button>
          {demoAvailable && !creatingAccount && (
            <a href="/demo" target="_blank" rel="noopener" className="mt-4 block w-fit text-sm font-medium text-slate-700 underline">
              Open demo in a new tab
            </a>
          )}
        </section>
      </div>
    </main>
  )
}

function SampleSource() {
  const article = sampleArticle(new URLSearchParams(location.search).get('id') ?? '')
  return <main className="sample-source">
    <Brand /><p className="topic">Fictional sample reporting</p>
    <h1>{article?.headline ?? 'Article unavailable'}</h1>
    {article && <><p className="muted">{article.publisher} · {article.published}</p>
      <p lang={article.language}>{article.body}</p></>}
  </main>
}

createRoot(document.getElementById('root')!).render(
  window.location.pathname === '/sample-source' ? <SampleSource /> : demoAvailable && window.location.pathname === '/demo'
    ? <main><NewsWorkspace owner="demo" /></main>
    : <App />,
)
