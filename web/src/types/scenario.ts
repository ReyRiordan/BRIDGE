/**
 * The `GET /scenario` contract (api/scenario.py serves exactly these 5 keys).
 *
 * Hand-written rather than generated: unlike the event envelope there is no
 * pydantic model upstream — the scenario JSON itself is the source of truth,
 * and the whitelist in api/scenario.py is the contract.
 *
 * No enums anywhere: tsconfig sets `erasableSyntaxOnly`.
 */

export interface ScenarioAction {
  type: string
  desc: string
  point_change: number
  /** Server-only (the referee decides whether an action sticks). Never read by the client. */
  persist: boolean
  /** Composite layer index, or null for actions with no scene art. */
  layer: number | null
  /** Filename under /visuals/, or null when the action has no art in that state. */
  active: string | null
  inactive: string | null
}

export interface PointBar {
  max: number
  start: number
  goal: number
}

export interface Scenario {
  intro: string
  goal: string
  actions: ScenarioAction[]
  point_bar: PointBar
  time_limit: number
}

/**
 * Shallow structural guard. Deliberately shallow: it exists so a malformed
 * body fails loudly on the Start screen (where there is a Retry button) rather
 * than as an undefined-read mid-game.
 */
export function isScenario(value: unknown): value is Scenario {
  if (typeof value !== 'object' || value === null) return false
  const s = value as Record<string, unknown>
  if (typeof s.intro !== 'string' || typeof s.goal !== 'string') return false
  if (typeof s.time_limit !== 'number') return false
  if (!Array.isArray(s.actions)) return false
  const bar = s.point_bar
  if (typeof bar !== 'object' || bar === null) return false
  const b = bar as Record<string, unknown>
  return (
    typeof b.max === 'number' &&
    typeof b.start === 'number' &&
    typeof b.goal === 'number'
  )
}
