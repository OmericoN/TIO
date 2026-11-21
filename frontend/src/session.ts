// The Supabase Auth session, kept in this browser so a reload keeps the listener signed in. Access tokens last an
// hour; the refresh token renews them before a backend call needs one.

export type AuthUser = { id: string; email?: string; user_metadata?: Record<string, unknown> }
export type Session = { accessToken: string; refreshToken: string; expiresAt: number; user: AuthUser }

const storageKey = 'tio-session'
const refreshMargin = 60 // seconds before expiry that a token counts as expired

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL?.trim().replace(/\/+$/, '')
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim()

/** A session from a Supabase Auth token or sign-up response, or null when it has no usable token and user. */
export function sessionFrom(response: unknown): Session | null {
  const body = response as { access_token?: unknown; refresh_token?: unknown; expires_at?: unknown; expires_in?: unknown; user?: unknown } | null
  const user = body?.user as AuthUser | undefined
  if (typeof body?.access_token !== 'string' || !body.access_token || typeof user?.id !== 'string' || !user.id) return null
  const now = Math.floor(Date.now() / 1000)
  const expiresAt = typeof body.expires_at === 'number' ? body.expires_at
    : now + (typeof body.expires_in === 'number' ? body.expires_in : 3600)
  return { accessToken: body.access_token, refreshToken: typeof body.refresh_token === 'string' ? body.refresh_token : '', expiresAt, user }
}

export function loadSession(): Session | null {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
    return typeof saved?.accessToken === 'string' && typeof saved.refreshToken === 'string'
      && typeof saved.expiresAt === 'number' && typeof saved.user?.id === 'string' ? saved : null
  } catch { return null }
}

export function saveSession(session: Session | null) {
  try {
    if (session) localStorage.setItem(storageKey, JSON.stringify(session))
    else localStorage.removeItem(storageKey)
  } catch { /* Without storage the session lasts until the page reloads. */ }
}

export function expiring(session: Session) {
  return session.expiresAt - Date.now() / 1000 < refreshMargin
}

/** A renewed session; 'rejected' when the refresh token no longer works (sign in again), 'offline' when Supabase
 * could not be reached (keep the session and try again later). */
export async function refreshSession(session: Session): Promise<Session | 'rejected' | 'offline'> {
  // Another tab may have renewed it already: refresh tokens are single use.
  const stored = loadSession()
  if (stored && stored.user.id === session.user.id && stored.refreshToken !== session.refreshToken && !expiring(stored)) return stored
  if (!session.refreshToken || !supabaseUrl || !publishableKey) return 'rejected'
  try {
    const response = await fetch(`${supabaseUrl}/auth/v1/token?grant_type=refresh_token`, {
      method: 'POST',
      headers: { apikey: publishableKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: session.refreshToken }),
      signal: AbortSignal.timeout(10_000),
    })
    if (response.status >= 500) return 'offline'
    return (response.ok && sessionFrom(await response.json())) || 'rejected'
  } catch { return 'offline' }
}

/** End the session on Supabase too, so its refresh token stops working. Best effort. */
export async function revokeSession(session: Session) {
  if (!supabaseUrl || !publishableKey) return
  await fetch(`${supabaseUrl}/auth/v1/logout`, {
    method: 'POST',
    headers: { apikey: publishableKey, Authorization: `Bearer ${session.accessToken}` },
    signal: AbortSignal.timeout(10_000),
  }).catch(() => null)
}
