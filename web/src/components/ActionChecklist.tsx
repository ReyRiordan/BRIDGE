import type { ChecklistRow, ChecklistStatus } from '../state/useGame'

interface ActionChecklistProps {
  rows: ChecklistRow[]
}

const ICON: Record<ChecklistStatus, string> = {
  'found-good': '✓',
  'found-bad': '✗',
  missed: '○',
}

const ICON_STYLE: Record<ChecklistStatus, string> = {
  'found-good': 'bg-good/15 text-good',
  'found-bad': 'bg-bad/15 text-bad',
  missed: 'bg-surface-raised text-ink-muted',
}

function ActionChecklist({ rows }: ActionChecklistProps) {
  return (
    <ul className="flex flex-col divide-y divide-edge">
      {rows.map((row) => (
        <li
          key={row.type}
          data-action={row.type}
          data-status={row.status}
          className="flex items-center gap-4 py-2.5"
        >
          <span
            aria-hidden="true"
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm ${ICON_STYLE[row.status]}`}
          >
            {ICON[row.status]}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-ink">
              {row.type}
            </span>
            <span className="block truncate text-xs text-ink-muted">
              {row.desc}
            </span>
          </span>
          <span
            className={`font-mono text-sm font-semibold tabular-nums ${
              row.pointChange < 0 ? 'text-good' : 'text-bad'
            }`}
          >
            {row.delta}
          </span>
        </li>
      ))}
    </ul>
  )
}

export default ActionChecklist
