import { getApiBaseUrl } from '../config'
import {
  isScenario,
  isScenarioSummaryList,
  type Scenario,
  type ScenarioSummary,
} from '../types/scenario'

/*
 * No retries in either call: the Start screen owns the error state and offers
 * Retry, which is a better signal to the user than a silent backoff.
 */

/** The picker list: one card per scenario. */
export async function fetchScenarios(): Promise<ScenarioSummary[]> {
  const res = await fetch(`${getApiBaseUrl()}/scenarios`)
  if (!res.ok) {
    throw new Error(`Failed to load scenarios (HTTP ${res.status})`)
  }
  const body: unknown = await res.json()
  if (!isScenarioSummaryList(body)) {
    throw new Error('Scenario list did not match the expected shape')
  }
  return body
}

/** The config the whole UI is derived from, for the scenario the student picked. */
export async function fetchScenario(id: string): Promise<Scenario> {
  const res = await fetch(
    `${getApiBaseUrl()}/scenario/${encodeURIComponent(id)}`,
  )
  if (!res.ok) {
    throw new Error(`Failed to load scenario (HTTP ${res.status})`)
  }
  const body: unknown = await res.json()
  if (!isScenario(body)) {
    throw new Error('Scenario response did not match the expected shape')
  }
  return body
}
