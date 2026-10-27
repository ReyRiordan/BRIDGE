/**
 * The `GET /scenarios` + `GET /scenario/{id}` contract (api/scenario.py serves
 * exactly these keys).
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
  /** Filename under /visuals/<id>/, or null when the action has no art in that state. */
  active: string | null
  inactive: string | null
  /** KEY action type: `point_change` once it has been taken, else `point_change_without`. */
  requires?: string
  point_change_without?: number
  /** END SIMULATION: taking it ends the game as `fail`, with `debrief` as the reason. */
  ends_simulation?: boolean
  debrief?: string
}

export interface PointBar {
  max: number
  start: number
  goal: number
}

/** Server-driven penalty: `point_change` per `seconds` without a helpful action. */
export interface Inactivity {
  seconds: number
  point_change: number
}

/** One Start-screen picker card (`GET /scenarios`). */
export interface ScenarioSummary {
  id: string
  title: string
  /** Filename under /visuals/<id>/. */
  intro_image: string
}

export interface Scenario {
  id: string
  title: string
  /** Filename under /visuals/<id>/. */
  intro_image: string
  /** Static plate under /visuals/<id>/, drawn below every layer; null = none. */
  environment?: string | null
  /** Extension of the `patient_<n>` frames; the server defaults it to `png`. */
  patient_frame_ext?: string
  inactivity?: Inactivity | null
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
  if (typeof s.id !== 'string' || typeof s.title !== 'string') return false
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

export function isScenarioSummaryList(
  value: unknown,
): value is ScenarioSummary[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item: unknown) =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as Record<string, unknown>).id === 'string' &&
        typeof (item as Record<string, unknown>).title === 'string' &&
        typeof (item as Record<string, unknown>).intro_image === 'string',
    )
  )
}
