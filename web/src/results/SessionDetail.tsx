import type { ReactNode } from 'react'
import type { Scenario } from '../types/scenario'
import EscalationChart from './EscalationChart'
import { ModeIcon, OutcomeBadge } from './badges'
import { downloadFile } from './download'
import { dateTime, latency, mmss, outcomeOf, signed, timeUsed } from './format'
import {
  checklistReport,
  chipLabel,
  chipTone,
  replyLatencies,
  type ChecklistReportRow,
  type ChecklistState,
  type ChipTone,
} from './report'
import type { SessionRecord, TurnRecord } from './types'

interface SessionDetailProps {
  record: SessionRecord
  /** The scenario config (`/scenario/{id}`), for the checklist; null while loading or on failure. */
  scenario: Scenario | null
  scenarioError?: string | null
}

const END_REASON_TEXT: Record<string, string> = {
  goal: 'Agitation reached the goal.',
  max: 'Agitation reached the maximum.',
  timeout: 'The time limit ran out.',
  abandoned: 'The connection closed before the game ended.',
}

/**
 * One session: the header, the agitation chart, the action checklist, the
 * annotated transcript and the raw-JSON download. Pure over its props; the
 * route in ResultsApp does the fetching.
 */
function SessionDetail({
  record,
  scenario,
  scenarioError,
}: SessionDetailProps) {
  const outcome = outcomeOf(record)
  const ending =
    record.reason ??
    (record.end_reason ? END_REASON_TEXT[record.end_reason] : undefined) ??
    (outcome === 'abandoned'
      ? END_REASON_TEXT.abandoned
      : outcome === 'never_connected'
        ? 'The student never connected.'
        : null)
  const max = record.max_escalation ?? scenario?.point_bar.max ?? 10
  const goal = record.goal ?? scenario?.point_bar.goal ?? 0

  function exportJson() {
    downloadFile(
      `${record.session_id}.json`,
      JSON.stringify(record, null, 2),
      'application/json',
    )
  }

  return (
    <article className="flex flex-col gap-6">
      <header className="flex flex-col gap-3 rounded-card border border-edge bg-surface p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-xl font-semibold text-ink">
            <span className="font-mono">{record.netid}</span>
            <span className="text-ink-muted"> · </span>
            {record.scenario_title}
          </h2>
          <OutcomeBadge outcome={outcome} />
          <button
            type="button"
            onClick={exportJson}
            className="ml-auto cursor-pointer rounded-full border border-edge bg-surface-raised px-4 py-2 text-xs font-semibold tracking-wide text-ink uppercase transition hover:border-accent focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none"
          >
            Download JSON
          </button>
        </div>
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <Fact label="Date">{dateTime(record.created_at)}</Fact>
          <Fact label="Mode">
            <ModeIcon mode={record.mode} />{' '}
            {record.mode === 'text' ? 'Text' : 'Voice'}
          </Fact>
          <Fact label="Agitation">
            <span className="font-mono tabular-nums">
              {record.start_escalation} → {record.escalation}
            </span>
          </Fact>
          <Fact label="Time">
            <span className="font-mono tabular-nums">{timeUsed(record)}</span>
          </Fact>
        </dl>
        {ending && <p className="text-sm text-ink-muted">{ending}</p>}
      </header>

      <section className="rounded-card border border-edge bg-surface p-5">
        <h3 className="mb-3 text-xs font-semibold tracking-[0.2em] text-ink-muted uppercase">
          Agitation over time
        </h3>
        {record.timeline.length > 0 ? (
          <EscalationChart
            timeline={record.timeline}
            turns={record.turns}
            max={max}
            goal={goal}
            timeLimit={record.time_limit_s}
            endT={record.duration_s}
          />
        ) : (
          <p className="text-sm text-ink-muted">No agitation was recorded.</p>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <section className="rounded-card border border-edge bg-surface p-5">
          <h3 className="mb-3 text-xs font-semibold tracking-[0.2em] text-ink-muted uppercase">
            Actions
          </h3>
          {scenario ? (
            <ActionChecklistReport
              rows={checklistReport(scenario.actions, record.turns)}
            />
          ) : (
            <p className="text-sm text-ink-muted">
              {scenarioError ?? 'Loading the scenario…'}
            </p>
          )}
        </section>

        <section className="rounded-card border border-edge bg-surface p-5">
          <h3 className="mb-3 text-xs font-semibold tracking-[0.2em] text-ink-muted uppercase">
            Transcript
          </h3>
          <TranscriptReport turns={record.turns} />
        </section>
      </div>
    </article>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="text-ink">{children}</dd>
    </div>
  )
}

const STATE_ICON: Record<ChecklistState, string> = {
  earned: '✓',
  attempted: '↻',
  missed: '○',
  occurred: '✗',
  avoided: '✓',
}

const STATE_STYLE: Record<ChecklistState, string> = {
  earned: 'bg-good/15 text-good',
  attempted: 'bg-surface-raised text-ink',
  missed: 'bg-surface-raised text-ink-muted',
  occurred: 'bg-bad/15 text-bad',
  avoided: 'bg-surface-raised text-ink-muted',
}

function stateNote(row: ChecklistReportRow): string {
  const repeats = row.repeats ? ` · repeated ${row.repeats}×` : ''
  switch (row.state) {
    case 'earned':
      return `Earned at ${mmss(row.firstT ?? 0)}, ${signed(row.points)}${repeats}`
    case 'attempted':
      return `Attempted ${row.count}×, no credit${repeats}`
    case 'missed':
      return 'Missed'
    case 'occurred':
      return `Occurred ${row.count}× (first at ${mmss(row.firstT ?? 0)}), ${signed(row.points)}`
    case 'avoided':
      return 'Avoided'
  }
}

function ActionChecklistReport({ rows }: { rows: ChecklistReportRow[] }) {
  const helpful = rows.filter((r) => !r.escalates)
  const escalating = rows.filter((r) => r.escalates)
  return (
    <div className="flex flex-col gap-4">
      {[
        { title: 'Helpful', items: helpful },
        { title: 'Escalating', items: escalating },
      ]
        .filter((g) => g.items.length > 0)
        .map((group) => (
          <div key={group.title}>
            <h4 className="mb-1 text-xs text-ink-muted">{group.title}</h4>
            <ul className="flex flex-col divide-y divide-edge">
              {group.items.map((row) => (
                <li
                  key={row.type}
                  data-action={row.type}
                  data-state={row.state}
                  className="flex items-start gap-3 py-2"
                >
                  <span
                    aria-hidden="true"
                    className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs ${STATE_STYLE[row.state]}`}
                  >
                    {STATE_ICON[row.state]}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-ink">
                      {row.type}
                    </span>
                    <span className="block text-xs text-ink-muted">
                      {stateNote(row)}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
    </div>
  )
}

const CHIP_STYLE: Record<ChipTone, string> = {
  good: 'border-good/40 text-good',
  bad: 'border-bad/40 text-bad',
  neutral: 'border-edge text-ink',
  repeat: 'border-edge text-ink-muted',
}

function TranscriptReport({ turns }: { turns: TurnRecord[] }) {
  if (turns.length === 0) {
    return <p className="text-sm text-ink-muted">Nothing was said.</p>
  }
  const latencies = replyLatencies(turns)
  return (
    <ol className="flex flex-col gap-3">
      {turns.map((turn, i) => {
        const student = turn.role === 'student'
        const gap = latencies.get(i)
        return (
          <li
            key={i}
            data-role={turn.role}
            className={`flex flex-col gap-1 ${student ? 'items-end' : 'items-start'}`}
          >
            <span className="flex items-center gap-2 text-[11px] text-ink-muted tabular-nums">
              <span>{mmss(turn.t)}</span>
              <span>{student ? 'Student' : 'Patient'}</span>
              {student && turn.source === 'text' && (
                <span
                  data-source="text"
                  role="img"
                  aria-label="Typed"
                  title="Typed"
                >
                  ⌨️
                </span>
              )}
              {gap !== undefined && (
                <span data-latency className="opacity-70">
                  {latency(gap)}
                </span>
              )}
            </span>
            <p
              className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed text-ink ${
                student
                  ? 'rounded-br-sm bg-student'
                  : 'rounded-bl-sm bg-patient'
              }`}
            >
              {turn.content}
            </p>
            {student && turn.actions.length > 0 && (
              <ul className="flex max-w-[85%] flex-wrap justify-end gap-1">
                {turn.actions.map((a, j) => (
                  <li
                    key={j}
                    data-chip={chipTone(a)}
                    className={`rounded-full border px-2 py-0.5 text-[11px] ${CHIP_STYLE[chipTone(a)]}`}
                  >
                    {chipLabel(a)}
                  </li>
                ))}
              </ul>
            )}
          </li>
        )
      })}
    </ol>
  )
}

export default SessionDetail
