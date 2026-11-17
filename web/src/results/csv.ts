import { OUTCOME_LABEL, outcomeOf } from './format'
import type { SessionSummary } from './types'

/**
 * The list view's export: the table's columns plus `session_id`, RFC 4180
 * (every value quoted, `"` doubled, CRLF rows). A cell starting with a formula
 * trigger (`= + - @`, or a tab / CR) is prefixed with `'` so a spreadsheet
 * shows it as text rather than running it: NetIDs are self-reported, so
 * nothing in a row can be trusted to be inert.
 */

const COLUMNS: [
  string,
  (row: SessionSummary) => string | number | undefined,
][] = [
  ['Date', (r) => r.created_at],
  ['NetID', (r) => r.netid],
  ['Scenario', (r) => r.scenario_title],
  ['Mode', (r) => r.mode],
  ['Outcome', (r) => OUTCOME_LABEL[outcomeOf(r)]],
  ['Start agitation', (r) => r.start_escalation],
  ['Final agitation', (r) => r.escalation],
  ['Time used (s)', (r) => r.duration_s],
  ['Time limit (s)', (r) => r.time_limit_s],
  ['Helpful', (r) => r.helpful_count],
  ['Escalating', (r) => r.escalating_count],
  ['Session ID', (r) => r.session_id],
]

const FORMULA_TRIGGER = /^[=+\-@\t\r]/

export function csvCell(value: string | number | null | undefined): string {
  let text = value === null || value === undefined ? '' : String(value)
  if (FORMULA_TRIGGER.test(text)) text = `'${text}`
  return `"${text.replace(/"/g, '""')}"`
}

export function toCsv(rows: SessionSummary[]): string {
  const lines = [
    COLUMNS.map(([name]) => csvCell(name)).join(','),
    ...rows.map((row) => COLUMNS.map(([, get]) => csvCell(get(row))).join(',')),
  ]
  return lines.join('\r\n') + '\r\n'
}
