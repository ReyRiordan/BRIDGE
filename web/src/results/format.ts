import type { SessionSummary } from './types'

/** Seconds → `mm:ss` (whole seconds, floored). */
export function mmss(t: number): string {
  const total = Math.max(0, Math.floor(t))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** A point delta with its sign: `−3` (a real minus), `+2`, `0`. */
export function signed(n: number): string {
  if (n < 0) return `−${Math.abs(n)}`
  if (n > 0) return `+${n}`
  return '0'
}

export type Outcome =
  | 'success'
  | 'max'
  | 'timeout'
  | 'end_action'
  | 'fail'
  | 'abandoned'
  | 'never_connected'
  | 'in_progress'

export const OUTCOMES: Outcome[] = [
  'success',
  'max',
  'timeout',
  'end_action',
  'fail',
  'abandoned',
  'never_connected',
  'in_progress',
]

export const OUTCOME_LABEL: Record<Outcome, string> = {
  success: 'Success',
  max: 'Escalated to max',
  timeout: 'Timed out',
  end_action: 'Ended by action',
  fail: 'Failed',
  abandoned: 'Abandoned',
  never_connected: 'Never connected',
  in_progress: 'In progress',
}

/** The badge a session shows, from its display status and end reason. */
export function outcomeOf(
  row: Pick<SessionSummary, 'display_status' | 'end_reason'>,
): Outcome {
  switch (row.display_status) {
    case 'success':
      return 'success'
    case 'abandoned':
      return 'abandoned'
    case 'never_connected':
      return 'never_connected'
    case 'started':
    case 'active':
      return 'in_progress'
    case 'fail':
      if (
        row.end_reason === 'max' ||
        row.end_reason === 'timeout' ||
        row.end_reason === 'end_action'
      ) {
        return row.end_reason
      }
      return 'fail'
    default:
      return 'fail'
  }
}

/** `03:32 / 05:00`, or `— / 05:00` when the session never recorded a duration. */
export function timeUsed(
  row: Pick<SessionSummary, 'duration_s' | 'time_limit_s'>,
): string {
  const used = typeof row.duration_s === 'number' ? mmss(row.duration_s) : '—'
  return `${used} / ${mmss(row.time_limit_s)}`
}

/** A readable local date and time. */
export function dateTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** The local calendar day (`YYYY-MM-DD`) of an ISO timestamp, for date filters. */
export function localDay(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** Patient reply latency, shown subtly: `+3.6 s`. */
export function latency(seconds: number): string {
  return `+${seconds.toFixed(1)} s`
}
