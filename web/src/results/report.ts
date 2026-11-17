import type { ScenarioAction } from '../types/scenario'
import { signed } from './format'
import type {
  RecordedAction,
  StudentTurn,
  TimelinePoint,
  TurnRecord,
} from './types'

/*
 * Pure derivations over one session record, shared by the detail view's chart,
 * checklist and transcript.
 */

export type ChipTone = 'good' | 'bad' | 'neutral' | 'repeat'

/** `Acknowledge autonomy −3`, or `Explain risks · repeat 0` for a repeat. */
export function chipLabel(action: RecordedAction): string {
  if (action.repeat) return `${action.type} · repeat 0`
  return `${action.type} ${signed(action.point_change)}`
}

export function chipTone(action: RecordedAction): ChipTone {
  if (action.repeat) return 'repeat'
  if (action.point_change < 0) return 'good'
  if (action.point_change > 0) return 'bad'
  return 'neutral'
}

export function studentTurns(turns: TurnRecord[]): StudentTurn[] {
  return turns.filter((t): t is StudentTurn => t.role === 'student')
}

/** Per turn index: a patient turn's gap from the student turn before it. */
export function replyLatencies(turns: TurnRecord[]): Map<number, number> {
  const out = new Map<number, number>()
  let lastStudent: number | null = null
  turns.forEach((turn, i) => {
    if (turn.role === 'student') {
      lastStudent = turn.t
    } else if (lastStudent !== null) {
      out.set(i, Math.max(0, turn.t - lastStudent))
      lastStudent = null
    }
  })
  return out
}

export type StepTone = 'helpful' | 'escalating' | 'inactivity'

export interface ChartStep {
  point: TimelinePoint
  from: number
  tone: StepTone
  /** The student turn that caused it (cause `turn`), when one is found. */
  turn?: StudentTurn
}

/** One step per timeline change after the start point. */
export function chartSteps(
  timeline: TimelinePoint[],
  turns: TurnRecord[],
): ChartStep[] {
  const students = studentTurns(turns)
  const steps: ChartStep[] = []
  for (let i = 1; i < timeline.length; i++) {
    const point = timeline[i]
    const from = timeline[i - 1].escalation
    const tone: StepTone =
      point.cause === 'inactivity'
        ? 'inactivity'
        : point.escalation < from
          ? 'helpful'
          : 'escalating'
    const turn =
      point.cause === 'turn'
        ? [...students].reverse().find((s) => Math.abs(s.t - point.t) < 0.05)
        : undefined
    steps.push({ point, from, tone, turn })
  }
  return steps
}

export type ChecklistState =
  'earned' | 'attempted' | 'missed' | 'occurred' | 'avoided'

export interface ChecklistReportRow {
  type: string
  desc: string
  /** An escalating action (positive points, or an END SIMULATION). */
  escalates: boolean
  state: ChecklistState
  /** When it was first credited (earned) or first said (occurred). */
  firstT?: number
  /** The summed effective points it applied. */
  points: number
  /** How many times it was detected. */
  count: number
  /** How many of those were uncredited repeats. */
  repeats: number
}

/**
 * Every scenario action against what the session recorded:
 * - helpful: `earned` (credited at least once), `attempted` (detected but
 *   never credited, e.g. only as a zero-point repeat), or `missed`;
 * - escalating: `occurred` (with how many times) or `avoided`.
 */
export function checklistReport(
  actions: ScenarioAction[],
  turns: TurnRecord[],
): ChecklistReportRow[] {
  const said = studentTurns(turns).flatMap((turn) =>
    turn.actions.map((action) => ({ action, t: turn.t })),
  )
  return actions.map((def) => {
    const escalates = def.point_change > 0 || def.ends_simulation === true
    const hits = said.filter((s) => s.action.type === def.type)
    const repeats = hits.filter((s) => s.action.repeat).length
    const points = hits.reduce((sum, s) => sum + s.action.point_change, 0)
    const base = {
      type: def.type,
      desc: def.desc,
      escalates,
      points,
      count: hits.length,
      repeats,
    }
    if (escalates) {
      return hits.length
        ? { ...base, state: 'occurred' as const, firstT: hits[0].t }
        : { ...base, state: 'avoided' as const }
    }
    const credited = hits.find(
      (s) => !s.action.repeat && s.action.point_change < 0,
    )
    if (credited)
      return { ...base, state: 'earned' as const, firstT: credited.t }
    return { ...base, state: hits.length ? 'attempted' : 'missed' }
  })
}
