import { localDay, outcomeOf, type Outcome } from './format'
import type { SessionSummary } from './types'

export interface ListFilters {
  query: string
  scenario: string
  outcome: Outcome | ''
  from: string
  to: string
  /** One student's history: an exact NetID, set by clicking it. */
  student: string | null
}

export const EMPTY_FILTERS: ListFilters = {
  query: '',
  scenario: '',
  outcome: '',
  from: '',
  to: '',
  student: null,
}

export function applyFilters(
  rows: SessionSummary[],
  f: ListFilters,
): SessionSummary[] {
  const q = f.query.trim().toLowerCase()
  return rows.filter((r) => {
    if (f.student !== null && r.netid !== f.student) return false
    if (q && !r.netid.toLowerCase().includes(q)) return false
    if (f.scenario && r.scenario_id !== f.scenario) return false
    if (f.outcome && outcomeOf(r) !== f.outcome) return false
    const day = localDay(r.created_at)
    if (f.from && day < f.from) return false
    if (f.to && day > f.to) return false
    return true
  })
}
