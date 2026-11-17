import { useMemo, useState } from 'react'
import { toCsv } from './csv'
import { downloadFile } from './download'
import {
  OUTCOME_LABEL,
  OUTCOMES,
  dateTime,
  outcomeOf,
  timeUsed,
  type Outcome,
} from './format'
import { OutcomeBadge, ModeIcon } from './badges'
import { EMPTY_FILTERS, applyFilters, type ListFilters } from './filters'
import type { SessionSummary } from './types'

type SortKey =
  | 'date'
  | 'netid'
  | 'scenario'
  | 'mode'
  | 'outcome'
  | 'agitation'
  | 'time'
  | 'counts'

interface Sort {
  key: SortKey
  dir: 'asc' | 'desc'
}

const SORT_VALUE: Record<SortKey, (r: SessionSummary) => string | number> = {
  date: (r) => r.created_at,
  netid: (r) => r.netid,
  scenario: (r) => r.scenario_title,
  mode: (r) => r.mode,
  outcome: (r) => OUTCOME_LABEL[outcomeOf(r)],
  agitation: (r) => r.escalation,
  time: (r) => r.duration_s ?? -1,
  counts: (r) => r.helpful_count - r.escalating_count,
}

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'date', label: 'Date' },
  { key: 'netid', label: 'NetID' },
  { key: 'scenario', label: 'Scenario' },
  { key: 'mode', label: 'Mode' },
  { key: 'outcome', label: 'Outcome' },
  { key: 'agitation', label: 'Agitation' },
  { key: 'time', label: 'Time' },
  { key: 'counts', label: 'Helpful / escalating' },
]

interface SessionListProps {
  sessions: SessionSummary[]
  filters: ListFilters
  onFiltersChange: (filters: ListFilters) => void
  onOpen: (sessionId: string) => void
}

const input =
  'rounded-lg border border-edge bg-surface-raised px-3 py-2 text-sm text-ink focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none'

/**
 * Every session, filterable and sortable, newest first by default. Clicking a
 * NetID narrows to that student's attempts in chronological order with a
 * sparkline of each attempt's final agitation (computed here, from the
 * summaries). The CSV exports exactly the rows on screen.
 */
function SessionList({
  sessions,
  filters,
  onFiltersChange,
  onOpen,
}: SessionListProps) {
  const [sort, setSort] = useState<Sort>({ key: 'date', dir: 'desc' })
  const set = (patch: Partial<ListFilters>) =>
    onFiltersChange({ ...filters, ...patch })

  const scenarios = useMemo(() => {
    const seen = new Map<string, string>()
    for (const s of sessions) seen.set(s.scenario_id, s.scenario_title)
    return [...seen].sort((a, b) => a[1].localeCompare(b[1]))
  }, [sessions])

  const rows = useMemo(() => {
    const filtered = applyFilters(sessions, filters)
    // A student's history reads oldest → newest unless re-sorted.
    const by =
      filters.student !== null && sort.key === 'date' && sort.dir === 'desc'
        ? ({ key: 'date', dir: 'asc' } as Sort)
        : sort
    const get = SORT_VALUE[by.key]
    const sign = by.dir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      const va = get(a)
      const vb = get(b)
      const cmp =
        typeof va === 'number' && typeof vb === 'number'
          ? va - vb
          : String(va).localeCompare(String(vb))
      return cmp * sign || a.created_at.localeCompare(b.created_at) * sign
    })
  }, [sessions, filters, sort])

  function toggleSort(key: SortKey) {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'date' ? 'desc' : 'asc' },
    )
  }

  function exportCsv() {
    const stamp = new Date().toISOString().slice(0, 10)
    downloadFile(
      `bridge-sessions-${stamp}.csv`,
      toCsv(rows),
      'text/csv;charset=utf-8',
    )
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          NetID
          <input
            type="search"
            value={filters.query}
            onChange={(e) => set({ query: e.target.value })}
            placeholder="Search"
            className={`${input} w-36`}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Scenario
          <select
            value={filters.scenario}
            onChange={(e) => set({ scenario: e.target.value })}
            className={input}
          >
            <option value="">All scenarios</option>
            {scenarios.map(([id, title]) => (
              <option key={id} value={id}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Outcome
          <select
            value={filters.outcome}
            onChange={(e) => set({ outcome: e.target.value as Outcome | '' })}
            className={input}
          >
            <option value="">All outcomes</option>
            {OUTCOMES.map((o) => (
              <option key={o} value={o}>
                {OUTCOME_LABEL[o]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          From
          <input
            type="date"
            value={filters.from}
            onChange={(e) => set({ from: e.target.value })}
            className={input}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          To
          <input
            type="date"
            value={filters.to}
            onChange={(e) => set({ to: e.target.value })}
            className={input}
          />
        </label>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-ink-muted tabular-nums">
            {rows.length} of {sessions.length}
          </span>
          <button
            type="button"
            onClick={exportCsv}
            disabled={rows.length === 0}
            className="cursor-pointer rounded-full border border-edge bg-surface-raised px-4 py-2 text-xs font-semibold tracking-wide text-ink uppercase transition hover:border-accent focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            Download CSV
          </button>
        </div>
      </div>

      {filters.student !== null && (
        <StudentStrip
          netid={filters.student}
          rows={applyFilters(sessions, {
            ...EMPTY_FILTERS,
            student: filters.student,
          })}
          onClear={() => set({ student: null })}
        />
      )}

      <div className="overflow-x-auto rounded-card border border-edge bg-surface">
        <table className="w-full min-w-[56rem] text-left text-sm">
          <thead className="border-b border-edge text-xs text-ink-muted">
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={
                    sort.key === c.key
                      ? sort.dir === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : 'none'
                  }
                  className="px-3 py-2.5 font-medium"
                >
                  <button
                    type="button"
                    onClick={() => toggleSort(c.key)}
                    className="cursor-pointer whitespace-nowrap hover:text-ink focus-visible:text-ink focus-visible:outline-none"
                  >
                    {c.label}
                    <span aria-hidden="true" className="ml-1 inline-block w-2">
                      {sort.key === c.key
                        ? sort.dir === 'asc'
                          ? '↑'
                          : '↓'
                        : ''}
                    </span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-edge">
            {rows.map((r) => (
              <tr
                key={r.session_id}
                data-session={r.session_id}
                className="cursor-pointer transition hover:bg-surface-raised"
                onClick={() => onOpen(r.session_id)}
              >
                <td className="px-3 py-2.5 whitespace-nowrap">
                  <a
                    href={`#/results/${encodeURIComponent(r.session_id)}`}
                    onClick={(e) => e.stopPropagation()}
                    className="text-ink hover:text-accent focus-visible:text-accent focus-visible:outline-none"
                  >
                    {dateTime(r.created_at)}
                  </a>
                </td>
                <td className="px-3 py-2.5">
                  <button
                    type="button"
                    data-netid={r.netid}
                    onClick={(e) => {
                      e.stopPropagation()
                      set({ student: r.netid })
                    }}
                    title={`Show ${r.netid}'s history`}
                    className="cursor-pointer font-mono text-accent hover:underline focus-visible:underline focus-visible:outline-none"
                  >
                    {r.netid}
                  </button>
                </td>
                <td className="px-3 py-2.5">{r.scenario_title}</td>
                <td className="px-3 py-2.5">
                  <ModeIcon mode={r.mode} />
                </td>
                <td className="px-3 py-2.5">
                  <OutcomeBadge outcome={outcomeOf(r)} />
                </td>
                <td className="px-3 py-2.5 font-mono whitespace-nowrap tabular-nums">
                  {r.start_escalation} → {r.escalation}
                </td>
                <td className="px-3 py-2.5 font-mono whitespace-nowrap tabular-nums">
                  {timeUsed(r)}
                </td>
                <td className="px-3 py-2.5 font-mono whitespace-nowrap tabular-nums">
                  <span className="text-good">{r.helpful_count}</span>
                  <span className="text-ink-muted"> / </span>
                  <span className="text-bad">{r.escalating_count}</span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={COLUMNS.length}
                  className="px-3 py-8 text-center text-ink-muted"
                >
                  No sessions match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/** One student's attempts: a count and a sparkline of final agitation. */
function StudentStrip({
  netid,
  rows,
  onClear,
}: {
  netid: string
  rows: SessionSummary[]
  onClear: () => void
}) {
  const attempts = [...rows].sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  )
  return (
    <div
      data-student={netid}
      className="flex flex-wrap items-center gap-4 rounded-card border border-edge bg-surface px-4 py-3"
    >
      <div className="text-sm">
        <span className="font-mono text-ink">{netid}</span>
        <span className="text-ink-muted">
          {' '}
          · {attempts.length} attempt{attempts.length === 1 ? '' : 's'}
        </span>
      </div>
      <Sparkline rows={attempts} />
      <button
        type="button"
        onClick={onClear}
        className="ml-auto cursor-pointer text-xs text-ink-muted hover:text-ink focus-visible:text-ink focus-visible:outline-none"
      >
        Show all students ✕
      </button>
    </div>
  )
}

function Sparkline({ rows }: { rows: SessionSummary[] }) {
  if (rows.length === 0) return null
  const w = 160
  const h = 32
  const top = Math.max(...rows.map((r) => r.max_escalation ?? 10), 1)
  const px = (i: number) =>
    rows.length === 1 ? w / 2 : 4 + (i / (rows.length - 1)) * (w - 8)
  const py = (e: number) => 4 + (1 - e / top) * (h - 8)
  const points = rows.map((r, i) => `${px(i)},${py(r.escalation)}`).join(' ')
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="h-8 w-40"
      role="img"
      aria-label={`Final agitation per attempt: ${rows.map((r) => r.escalation).join(', ')}`}
    >
      <polyline
        points={points}
        fill="none"
        className="stroke-ink-muted"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
      {rows.map((r, i) => (
        <circle
          key={r.session_id}
          cx={px(i)}
          cy={py(r.escalation)}
          r={3}
          className={i === rows.length - 1 ? 'fill-accent' : 'fill-ink-muted'}
        >
          <title>
            {dateTime(r.created_at)}: {r.escalation}
          </title>
        </circle>
      ))}
    </svg>
  )
}

export default SessionList
