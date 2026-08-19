/**
 * Every derived visual, as pure functions of GameState.
 *
 * Lives in a `.ts` (not `.tsx`) file on purpose: components may only export
 * components (`react-refresh/only-export-components`), so all the shared
 * derivation has to sit outside the component tree — which is also what makes
 * it testable without a DOM.
 */
import type { GameState, LastAction } from './gameState'
import type { ScenarioAction } from '../types/scenario'

const VISUALS = '/visuals/'

/** Lowest and highest patient frames that exist in public/visuals/. */
const PATIENT_MIN = 0
const PATIENT_MAX = 10

/** Sorts before every real layer value in scenario_1 except a negative one. */
const PATIENT_LAYER = 0
export const PATIENT_KEY = '__patient__'

export function patientSrc(escalation: number): string {
  const frame = Math.min(
    PATIENT_MAX,
    Math.max(PATIENT_MIN, Math.round(escalation)),
  )
  return `${VISUALS}patient_${frame}.png`
}

export const patientFrameSources: string[] = Array.from(
  { length: PATIENT_MAX - PATIENT_MIN + 1 },
  (_, i) => `${VISUALS}patient_${PATIENT_MIN + i}.png`,
)

export interface SceneLayerView {
  /** Action `type`, or PATIENT_KEY for the synthetic patient layer. */
  key: string
  layer: number
  /** 1-based stacking order after sorting. */
  z: number
  /** Resolved image path, or null when this layer renders nothing. */
  src: string | null
}

/**
 * The composite, back to front.
 *
 * Actions carrying a `layer` are stacked together with a synthetic patient
 * layer at 0, sorted ascending. The sort is stable and the patient entry is
 * appended last, so ties resolve by the scenario's `actions` array order and
 * an action at layer 0 would sit behind the patient.
 *
 * For scenario_1 that yields: Environmental (−1) → patient (0) → Caregiver →
 * Force IV → Restraint (all layer 1, in file order).
 */
export function selectLayers(state: GameState): SceneLayerView[] {
  const actions: ScenarioAction[] = state.scenario?.actions ?? []
  const entries: Omit<SceneLayerView, 'z'>[] = []

  for (const action of actions) {
    if (action.layer === null) continue
    const file = state.activeActions.includes(action.type)
      ? action.active
      : action.inactive
    entries.push({
      key: action.type,
      layer: action.layer,
      src: file ? `${VISUALS}${file}` : null,
    })
  }

  entries.push({
    key: PATIENT_KEY,
    layer: PATIENT_LAYER,
    src: patientSrc(state.escalation),
  })

  return entries
    .slice()
    .sort((a, b) => a.layer - b.layer)
    .map((entry, index) => ({ ...entry, z: index + 1 }))
}

export type EscTone = 'calm' | 'watch' | 'warn' | 'crit'

export interface EscalationView {
  value: number
  max: number
  pct: number
  tone: EscTone
}

/** Note the inversion: low escalation is the *good* end, so it reads green. */
export function selectEscalation(state: GameState): EscalationView {
  const pct = state.max > 0 ? (state.escalation / state.max) * 100 : 0
  const tone: EscTone =
    pct < 30 ? 'calm' : pct < 60 ? 'watch' : pct < 80 ? 'warn' : 'crit'
  return { value: state.escalation, max: state.max, pct, tone }
}

export interface ClockView {
  remaining: number
  text: string
  urgent: boolean
}

const URGENT_SECONDS = 30

export function selectClock(state: GameState): ClockView {
  const remaining = Math.max(0, state.timer.limit - state.timer.elapsed)
  const minutes = Math.floor(remaining / 60)
  const seconds = Math.floor(remaining % 60)
  return {
    remaining,
    text: `${minutes}:${String(seconds).padStart(2, '0')}`,
    urgent: remaining < URGENT_SECONDS,
  }
}

/** The student has spoken and the patient has not answered yet. */
export function selectAwaitingPatient(state: GameState): boolean {
  const last = state.transcript[state.transcript.length - 1]
  return last?.role === 'student'
}

export type ChecklistStatus = 'missed' | 'found-good' | 'found-bad'

export interface ChecklistRow {
  type: string
  desc: string
  pointChange: number
  /** Signed, e.g. `-3` / `+4`. Rendered green when the change is negative. */
  delta: string
  status: ChecklistStatus
}

/** Scenario order, always the full action list — misses are the point. */
export function selectChecklist(state: GameState): ChecklistRow[] {
  const actions: ScenarioAction[] = state.scenario?.actions ?? []
  return actions.map((action) => {
    const taken = state.actionsTaken.has(action.type)
    const status: ChecklistStatus = !taken
      ? 'missed'
      : action.point_change > 0
        ? 'found-bad'
        : 'found-good'
    return {
      type: action.type,
      desc: action.desc,
      pointChange: action.point_change,
      delta: `${action.point_change > 0 ? '+' : ''}${action.point_change}`,
      status,
    }
  })
}

export interface ViewModel {
  phase: GameState['phase']
  escalation: EscalationView
  layers: SceneLayerView[]
  badge: LastAction | null
  transcript: GameState['transcript']
  clock: ClockView
  awaitingPatient: boolean
  actionsTaken: string[]
  activeActions: string[]
  status: string
  gameOver: GameState['gameOver']
  checklist: ChecklistRow[]
}

/**
 * Plain-JSON projection of everything the UI draws — the surface the golden
 * run asserts against, so semantics are locked independently of any JSX.
 */
export function toViewModel(state: GameState): ViewModel {
  return {
    phase: state.phase,
    escalation: selectEscalation(state),
    layers: selectLayers(state),
    badge: state.lastAction,
    transcript: state.transcript,
    clock: selectClock(state),
    awaitingPatient: selectAwaitingPatient(state),
    actionsTaken: [...state.actionsTaken],
    activeActions: state.activeActions,
    status: state.status,
    gameOver: state.gameOver,
    checklist: selectChecklist(state),
  }
}
