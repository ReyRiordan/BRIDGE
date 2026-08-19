import { API_BASE_URL } from '../config'
import { isScenario, type Scenario } from '../types/scenario'

/**
 * Fetch the scenario config the whole UI is derived from.
 *
 * No retries: the Start screen owns the error state and offers Retry, which is
 * a better signal to the user than a silent backoff.
 */
export async function fetchScenario(): Promise<Scenario> {
  const res = await fetch(`${API_BASE_URL}/scenario`)
  if (!res.ok) {
    throw new Error(`Failed to load scenario (HTTP ${res.status})`)
  }
  const body: unknown = await res.json()
  if (!isScenario(body)) {
    throw new Error('Scenario response did not match the expected shape')
  }
  return body
}
