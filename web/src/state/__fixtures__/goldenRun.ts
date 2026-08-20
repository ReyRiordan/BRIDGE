/**
 * Hand-authored golden runs — the acceptance surface for the game UI.
 *
 * Deliberately hand-written rather than recorded: a recording captures whatever
 * the runtime did that day, while these are chosen to hit every semantic edge
 * the UI has to survive. `successRun` covers, in order:
 *
 *   - the connect-time authoritative `state_update`
 *   - a `timer` tick interleaved mid-turn
 *   - a transient action (Force IV lights in one state_update, is gone in the
 *     next → its layer src goes back to null)
 *   - a multi-action turn (Caregiver + Acknowledge in one turn)
 *   - a persistent action layer (Caregiver stays lit for the rest of the run)
 *   - a repeated action (`actionsTaken` dedupes; the badge id still advances)
 *   - the descent to 0 with `game_over` arriving BEFORE the closing patient line
 *
 * Event order per turn matches runtime/bridge/app.py: transcript(student) →
 * action_detected×N → state_update → optional game_over → transcript(patient).
 */
import type { GameEvent } from '../../voice/gameEvents.gen'
import { gameReducer, initialState, type GameState } from '../gameState'
import { scenarioFixture } from './scenario.fixture'

const TS = '2026-01-01T00:00:00Z'

const student = (content: string): GameEvent => ({
  v: 1,
  type: 'transcript_update',
  role: 'student',
  content,
  timestamp: TS,
})

const patient = (content: string): GameEvent => ({
  v: 1,
  type: 'transcript_update',
  role: 'patient',
  content,
  timestamp: TS,
})

const detected = (
  action_type: string,
  desc: string,
  point_change: number,
): GameEvent => ({
  v: 1,
  type: 'action_detected',
  action_type,
  desc,
  point_change,
})

const state = (
  escalation: number,
  active_actions: string[],
  status: string,
): GameEvent => ({
  v: 1,
  type: 'state_update',
  escalation,
  max: 10,
  active_actions,
  status,
})

const tick = (elapsed: number): GameEvent => ({
  v: 1,
  type: 'timer',
  elapsed,
  limit: 300,
})

const CAREGIVER = 'Caregiver involvement'
const ENVIRONMENTAL = 'Environmental'

export const successRun: GameEvent[] = [
  // Connect-time authoritative state.
  state(5, [], 'agitated'),
  tick(0),

  // Turn 1 — a misstep: Force IV lights up.
  student('Let me just get this IV in quickly.'),
  detected('Force IV', 'Attempt IV while agitated', 4),
  state(9, ['Force IV'], 'escalated'),
  patient('No! Stop! Get away from me!'),

  // Turn 2 — two actions in one turn, with a timer tick landing mid-turn.
  // The Force IV layer clears here; the caregiver layer persists from now on.
  student('I can see this is overwhelming. Could you help me calm him?'),
  tick(12),
  detected(
    CAREGIVER,
    'Ask caregiver for guidance or involve them in calming',
    -3,
  ),
  detected('Acknowledge distress', 'E.g. “I see this is overwhelming”', -1),
  state(5, [CAREGIVER], 'settling'),
  patient('...he does that when it gets loud.'),

  // Turn 3 — Verbal Communication, first time.
  student('I am going to explain everything before I do it, okay?'),
  detected(
    'Verbal Communication',
    'Calm tone, simple explanations, reassurance',
    -1,
  ),
  state(4, [CAREGIVER], 'settling'),
  patient('Okay. Slow.'),

  // Turn 4 — Environmental: a second persistent layer.
  student('Let me dim these lights and ask the others to step out.'),
  detected(ENVIRONMENTAL, 'Dim lights, reduce noise, limit staff', -2),
  state(2, [CAREGIVER, ENVIRONMENTAL], 'calmer'),
  patient('That is better.'),

  // Turn 5 — Verbal Communication REPEATS (dedupes in actionsTaken, new badge
  // id), and game_over lands before the closing patient line.
  student('You are safe here. Would you like me to explain or show you first?'),
  detected(
    'Verbal Communication',
    'Calm tone, simple explanations, reassurance',
    -1,
  ),
  detected('Offer Control', 'Give choices (explain vs show)', -1),
  state(0, [CAREGIVER, ENVIRONMENTAL], 'calm'),
  {
    v: 1,
    type: 'game_over',
    status: 'success',
    reason: 'The patient is calm and ready to continue care.',
  },
  patient('Okay. You can explain it.'),
]

/** The other terminal path: escalation driven to max, with an urgent clock. */
export const failRun: GameEvent[] = [
  state(5, [], 'agitated'),
  tick(285),

  student('We need to do this now.'),
  detected('Authoritative tone', 'E.g. “We need to do this now”', 2),
  state(7, [], 'escalated'),
  patient('No! No no no!'),

  student('Get me the restraints.'),
  detected('Restraint', 'Chemical Sedations or Physical Restraints', 10),
  state(10, ['Restraint'], 'peak'),
  {
    v: 1,
    type: 'game_over',
    status: 'fail',
    reason: 'The patient was restrained.',
  },
  patient('(screaming)'),
]

/**
 * Envelope violations the reducer must drop without touching state: a future
 * envelope version, and an event type this build has never heard of.
 */
export const noisyEvents = [
  {
    v: 2,
    type: 'state_update',
    escalation: 3,
    max: 10,
    active_actions: [],
    status: 'x',
  },
  { v: 1, type: 'vitals_update', hr: 120 },
  {
    type: 'state_update',
    escalation: 1,
    max: 10,
    active_actions: [],
    status: 'y',
  },
] as unknown as GameEvent[]

/** A state that has loaded the scenario and begun — where every run starts. */
export const gameBaseState: GameState = gameReducer(
  gameReducer(initialState, {
    type: 'SCENARIO_LOADED',
    scenario: scenarioFixture,
  }),
  { type: 'BEGIN' },
)

/** Fold the first `n` events of a run (default: all of them). */
export function foldTo(
  events: GameEvent[],
  n: number = events.length,
): GameState {
  return events.slice(0, n).reduce(gameReducer, gameBaseState)
}
