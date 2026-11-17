import { getApiBaseUrl } from '../config'
import {
  isSessionRecord,
  isSessionSummaryList,
  type SessionRecord,
  type SessionSummary,
} from './types'

/**
 * The results API client. The server checks the password and every token
 * (`api/admin.py`); the SPA only carries the token, in `sessionStorage`, so
 * closing the tab logs out. Any 401 surfaces as `Unauthorized`, on which the
 * page drops the token and shows the login form again.
 */

const TOKEN_KEY = 'bridge.adminToken'

export class Unauthorized extends Error {
  constructor(message = 'Sign in again.') {
    super(message)
    this.name = 'Unauthorized'
  }
}

export function loadToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function saveToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token)
  } catch {
    // Storage blocked: the token lives only as long as this page's state.
  }
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // Nothing stored.
  }
}

/** Trade the shared password for a token. Wrong password → `Unauthorized`. */
export async function login(password: string): Promise<string> {
  const res = await fetch(`${getApiBaseUrl()}/admin/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  if (res.status === 401) throw new Unauthorized('Wrong password.')
  if (res.status === 503) {
    throw new Error('The results page is not configured on this backend.')
  }
  if (!res.ok) throw new Error(`Sign-in failed (HTTP ${res.status})`)
  const body = (await res.json()) as { token?: unknown } | null
  if (typeof body?.token !== 'string') {
    throw new Error('Sign-in response did not match the expected shape')
  }
  return body.token
}

async function getJson(token: string, path: string): Promise<unknown> {
  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (res.status === 401) throw new Unauthorized()
  if (res.status === 404) throw new Error('No such session.')
  if (!res.ok) throw new Error(`Request failed (HTTP ${res.status})`)
  return res.json()
}

export async function listSessions(token: string): Promise<SessionSummary[]> {
  const body = (await getJson(token, '/admin/sessions')) as {
    sessions?: unknown
  } | null
  if (!isSessionSummaryList(body?.sessions)) {
    throw new Error('Session list did not match the expected shape')
  }
  return body.sessions
}

export async function getSession(
  token: string,
  id: string,
): Promise<SessionRecord> {
  const body = await getJson(token, `/admin/sessions/${encodeURIComponent(id)}`)
  if (!isSessionRecord(body)) {
    throw new Error('Session record did not match the expected shape')
  }
  return body
}
