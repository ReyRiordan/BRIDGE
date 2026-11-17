/**
 * The student's self-reported identity: a Rutgers NetID plus the input mode.
 *
 * Remembered in `localStorage` under `bridge.identity` so a student signs in
 * once and runs several cases. Every storage access is wrapped: a private
 * window or blocked site data just means signing in again.
 *
 * `normalizeNetId` matches `normalize_netid` in `api/sessions.py`, which
 * re-checks on the server.
 */

export type Mode = 'voice' | 'text'

export interface Identity {
  netid: string
  mode: Mode
}

export const IDENTITY_KEY = 'bridge.identity'

const NETID = /^[a-z]+[0-9]*$/

/** Trim, lowercase, drop an `@…` suffix; null unless it looks like a NetID. */
export function normalizeNetId(raw: string): string | null {
  const netid = raw.trim().toLowerCase().split('@', 1)[0].trim()
  return netid.length >= 2 && netid.length <= 20 && NETID.test(netid)
    ? netid
    : null
}

function isIdentity(value: unknown): value is Identity {
  const v = value as Partial<Identity> | null
  return (
    typeof v?.netid === 'string' &&
    normalizeNetId(v.netid) === v.netid &&
    (v.mode === 'voice' || v.mode === 'text')
  )
}

export function loadIdentity(): Identity | null {
  try {
    const raw = localStorage.getItem(IDENTITY_KEY)
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    return isIdentity(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function saveIdentity(identity: Identity): void {
  try {
    localStorage.setItem(IDENTITY_KEY, JSON.stringify(identity))
  } catch {
    // Not remembered; the student signs in again next visit.
  }
}

export function clearIdentity(): void {
  try {
    localStorage.removeItem(IDENTITY_KEY)
  } catch {
    // Nothing to clear.
  }
}
