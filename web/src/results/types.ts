/**
 * The results API contract (`api/admin.py`), mirroring the session-record
 * schema in docs/backend/README.md. The list endpoint returns summaries (the
 * GSI projection); the detail endpoint returns the whole item. Both add
 * `display_status`, the server's on-read derivation of abandoned / never
 * connected sessions.
 */

export type SessionMode = 'voice' | 'text'

export type StoredStatus =
  'started' | 'active' | 'success' | 'fail' | 'abandoned'

export type DisplayStatus = StoredStatus | 'never_connected'

export type EndReason = 'goal' | 'max' | 'timeout' | 'end_action' | 'abandoned'

export interface SessionSummary {
  session_id: string
  created_at: string
  netid: string
  scenario_id: string
  scenario_title: string
  mode: SessionMode
  status: StoredStatus
  display_status: DisplayStatus
  end_reason: EndReason | null
  start_escalation: number
  escalation: number
  /** Set on connect; absent while `started`. */
  max_escalation?: number
  time_limit_s: number
  started_at?: string
  duration_s?: number
  helpful_count: number
  escalating_count: number
  updated_at: string
}

export interface RecordedAction {
  type: string
  /** The effective delta the referee applied (0 for a repeat). */
  point_change: number
  repeat: boolean
}

export interface StudentTurn {
  role: 'student'
  /** Seconds since game start (the on-screen clock). */
  t: number
  at: string
  content: string
  source?: SessionMode
  actions: RecordedAction[]
  escalation_after?: number
}

export interface PatientTurn {
  role: 'patient'
  t: number
  at: string
  content: string
}

export type TurnRecord = StudentTurn | PatientTurn

export interface TimelinePoint {
  t: number
  escalation: number
  cause: 'start' | 'turn' | 'inactivity'
}

export interface SessionRecord extends SessionSummary {
  kind?: string
  user_agent?: string
  reason: string | null
  goal?: number
  ended_at?: string
  turns: TurnRecord[]
  timeline: TimelinePoint[]
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/** Shallow, like the scenario guards: enough to fail loudly on a wrong shape. */
export function isSessionSummary(value: unknown): value is SessionSummary {
  return (
    isObject(value) &&
    typeof value.session_id === 'string' &&
    typeof value.created_at === 'string' &&
    typeof value.netid === 'string' &&
    typeof value.display_status === 'string'
  )
}

export function isSessionSummaryList(
  value: unknown,
): value is SessionSummary[] {
  return Array.isArray(value) && value.every(isSessionSummary)
}

export function isSessionRecord(value: unknown): value is SessionRecord {
  return (
    isSessionSummary(value) &&
    Array.isArray((value as unknown as Record<string, unknown>).turns) &&
    Array.isArray((value as unknown as Record<string, unknown>).timeline)
  )
}
