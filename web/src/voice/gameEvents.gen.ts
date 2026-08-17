// GENERATED — do not edit.
//
// Source of truth: runtime/bridge/events.py
// Regenerate:      python3 scripts/gen_event_types.py
//
// The v1 envelope for every message the voice runtime pushes over the WebRTC
// data channel. Backend CI fails if this file drifts from the pydantic models.

/** One finalized conversational turn, pushed as it happens. */
export interface TranscriptUpdate {
  v: 1;
  type: 'transcript_update';
  role: 'student' | 'patient';
  content: string;
  timestamp: string;
}

/** The authoritative game state after a turn was refereed. */
export interface StateUpdate {
  v: 1;
  type: 'state_update';
  escalation: number;
  max: number;
  active_actions: string[];
  status: string;
}

/** A de-escalation (or escalating) action the referee scored this turn. */
export interface ActionDetected {
  v: 1;
  type: 'action_detected';
  action_type: string;
  desc: string;
  point_change: number;
}

/** Session clock, in seconds. */
export interface Timer {
  v: 1;
  type: 'timer';
  elapsed: number;
  limit: number;
}

/** Terminal event: escalation hit 0 (success) or 10 / time ran out (fail). */
export interface GameOver {
  v: 1;
  type: 'game_over';
  status: 'success' | 'fail';
  reason: string;
}

export type GameEvent =
  | TranscriptUpdate
  | StateUpdate
  | ActionDetected
  | Timer
  | GameOver;
