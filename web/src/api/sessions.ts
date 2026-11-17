import { getApiBaseUrl } from '../config'
import type { Mode } from '../identity'

export interface CreateSessionBody {
  netid: string
  scenario_id: string
  mode: Mode
}

/**
 * Create the session record and get its server-issued id
 * (`<scenario_id>--<uuid>`), which every voice call then uses. No retry: the
 * intro screen surfaces the failure and the student tries Begin again.
 */
export async function createSession(
  body: CreateSessionBody,
): Promise<{ session_id: string }> {
  const res = await fetch(`${getApiBaseUrl()}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    throw new Error(`Failed to create the session (HTTP ${res.status})`)
  }
  const payload: unknown = await res.json()
  const id = (payload as { session_id?: unknown } | null)?.session_id
  if (typeof id !== 'string' || !id.startsWith(`${body.scenario_id}--`)) {
    throw new Error('Session response did not match the expected shape')
  }
  return { session_id: id }
}
