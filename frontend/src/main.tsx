import { useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL?.trim().replace(/\/+$/, '')
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim()
const configured = Boolean(supabaseUrl && publishableKey?.startsWith('sb_publishable_'))

function App() {
  const [signedIn, setSignedIn] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!configured || pending) return

    const form = new FormData(event.currentTarget)
    setError('')
    setPending(true)

    try {
      const response = await fetch(`${supabaseUrl}/auth/v1/token?grant_type=password`, {
        method: 'POST',
        headers: {
          apikey: publishableKey,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email: String(form.get('email')).trim(),
          password: form.get('password'),
        }),
      })

      if (!response.ok) {
        const failure = await response.json().catch(() => null)
        if (failure?.error_code === 'invalid_credentials') {
          setError('Email or password is incorrect.')
        } else if (failure?.error_code === 'email_not_confirmed') {
          setError('Confirm your email before signing in.')
        } else if (response.status === 429) {
          setError('Too many sign-in attempts. Try again in a moment.')
        } else {
          setError('Sign-in failed. Try again.')
        }
        return
      }

      const session = await response.json()
      if (typeof session.access_token !== 'string' || !session.access_token) {
        setError('Sign-in failed. Try again.')
        return
      }
      setSignedIn(true)
    } catch {
      setError('Could not reach Supabase. Try again.')
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <main className="min-h-dvh bg-slate-50" inert={!signedIn} />
      {!signedIn && (
        <div className="fixed inset-0 grid place-items-center overflow-y-auto bg-slate-950/30 p-4">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="login-title"
            className="w-full max-w-sm rounded-2xl bg-white p-7 text-slate-950 shadow-xl"
          >
            <h1 id="login-title" className="text-2xl font-semibold">Sign in to TIO</h1>
            <form onSubmit={signIn} className="mt-6 space-y-4">
              <label className="block text-sm font-medium">
                Email
                <input
                  type="email"
                  name="email"
                  autoComplete="username"
                  autoFocus
                  required
                  className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              <label className="block text-sm font-medium">
                Password
                <input
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  required
                  className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              {!configured && <p className="text-sm text-slate-600">Supabase sign-in is not configured.</p>}
              {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
              <button
                type="submit"
                disabled={!configured || pending}
                className="w-full rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
              >
                {pending ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
          </section>
        </div>
      )}
    </>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
