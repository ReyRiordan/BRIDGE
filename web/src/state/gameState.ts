/**
 * The single source of UI state: one pure reducer over the generated wire
 * events plus a small set of control actions.
 *
 * Two rules hold this design together:
 *
 * 1. **Everything is scenario-derived.** No escalation max, start value, time
 *    limit or action list is hardcoded here — they all come from `/scenario`
 *    via SCENARIO_LOADED. Editing the scenario JSON changes the UI with zero
 *    code edits.
 * 2. **No wall-clock in the reducer.** Timed behaviour (the 3 s badge auto-hide,
 *    the 600 ms end-screen handoff) lives in components. The reducer is a pure
 *    function of (state, action) and is fully exercisable in a test.
 *
 * The control-action names are SCREAMING_SNAKE and the wire discriminants are
 * lower_snake, so the two never collide: [Rewrite G] can hand the raw
 * `JSON.parse(msg.data)` straight to `dispatch` with no adapter layer.
 */
import type { GameEvent } from '../voice/gameEvents.gen'
import type { Scenario } from '../types/scenario'

export type Phase = 'start' | 'intro' | 'game' | 'end'

export interface TranscriptEntry {
  id: number
  role: 'student' | 'patient'
  content: string
  timestamp: string
}

export interface LastAction {
  /** Fresh on every detection — the badge is keyed by it so repeats re-pop. */
  id: number
  actionType: string
  desc: string
  pointChange: number
}

export interface GameOverState {
  status: 'success' | 'fail'
  reason: string
}

export interface GameState {
  phase: Phase
  scenario: Scenario | null
  escalation: number
  max: number
  activeActions: string[]
  /** The runtime's terse status string. Stored for [Rewrite G]; unrendered by design. */
  status: string
  transcript: TranscriptEntry[]
  timer: { elapsed: number; limit: number }
  actionsTaken: Set<string>
  lastAction: LastAction | null
  gameOver: GameOverState | null
  /** Monotonic id source for transcript entries and badge remounts. */
  seq: number
}

export type ControlAction =
  | { type: 'SCENARIO_LOADED'; scenario: Scenario }
  | { type: 'BEGIN' }
  | { type: 'SHOW_END' }
  | { type: 'PLAY_AGAIN' }

export type GameAction = ControlAction | GameEvent

export const initialState: GameState = {
  phase: 'start',
  scenario: null,
  escalation: 0,
  max: 0,
  activeActions: [],
  status: '',
  transcript: [],
  timer: { elapsed: 0, limit: 0 },
  actionsTaken: new Set<string>(),
  lastAction: null,
  gameOver: null,
  seq: 0,
}

const CONTROL_TYPES = new Set<string>([
  'SCENARIO_LOADED',
  'BEGIN',
  'SHOW_END',
  'PLAY_AGAIN',
])

const WIRE_TYPES = new Set<string>([
  'transcript_update',
  'state_update',
  'action_detected',
  'timer',
  'game_over',
])

/**
 * Envelope tolerance: an unknown `type` or a `v` other than 1 is dropped and
 * the *identical* state object is returned, so React re-renders nothing. A
 * future runtime can add events (or bump the envelope) without breaking a
 * deployed SPA.
 */
function isAcceptable(action: GameAction): boolean {
  if (CONTROL_TYPES.has(action.type)) return true
  const v = (action as { v?: unknown }).v
  return v === 1 && WIRE_TYPES.has(action.type)
}

export function gameReducer(state: GameState, action: GameAction): GameState {
  if (!isAcceptable(action)) {
    if (import.meta.env.DEV) {
      console.warn('[bridge] ignoring unrecognized event', action)
    }
    return state
  }

  switch (action.type) {
    case 'SCENARIO_LOADED': {
      const { point_bar, time_limit } = action.scenario
      return {
        ...initialState,
        actionsTaken: new Set<string>(),
        phase: 'intro',
        scenario: action.scenario,
        escalation: point_bar.start,
        max: point_bar.max,
        timer: { elapsed: 0, limit: time_limit },
      }
    }

    case 'BEGIN':
      return state.phase === 'intro' ? { ...state, phase: 'game' } : state

    case 'SHOW_END':
      return state.phase === 'game' && state.gameOver !== null
        ? { ...state, phase: 'end' }
        : state

    case 'PLAY_AGAIN':
      return { ...initialState, actionsTaken: new Set<string>() }

    case 'state_update':
      return {
        ...state,
        escalation: action.escalation,
        max: action.max,
        activeActions: action.active_actions,
        status: action.status,
      }

    case 'action_detected': {
      const seq = state.seq + 1
      const actionsTaken = new Set(state.actionsTaken)
      actionsTaken.add(action.action_type)
      return {
        ...state,
        seq,
        actionsTaken,
        lastAction: {
          id: seq,
          actionType: action.action_type,
          desc: action.desc,
          pointChange: action.point_change,
        },
      }
    }

    case 'transcript_update': {
      const seq = state.seq + 1
      return {
        ...state,
        seq,
        transcript: [
          ...state.transcript,
          {
            id: seq,
            role: action.role,
            content: action.content,
            timestamp: action.timestamp,
          },
        ],
      }
    }

    case 'timer':
      return {
        ...state,
        timer: { elapsed: action.elapsed, limit: action.limit },
      }

    // Terminal, and deliberately NOT a phase change: GameScreen holds the final
    // frame for 600 ms before dispatching SHOW_END.
    case 'game_over':
      return state.gameOver
        ? state
        : {
            ...state,
            gameOver: { status: action.status, reason: action.reason },
          }
  }
}
