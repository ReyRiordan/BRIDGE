import { OUTCOME_LABEL, type Outcome } from './format'
import type { SessionMode } from './types'

const OUTCOME_STYLE: Record<Outcome, string> = {
  success: 'bg-good/15 text-good',
  max: 'bg-bad/15 text-bad',
  timeout: 'bg-bad/15 text-bad',
  end_action: 'bg-bad/15 text-bad',
  fail: 'bg-bad/15 text-bad',
  abandoned: 'bg-surface-raised text-ink-muted',
  never_connected: 'bg-surface-raised text-ink-muted',
  in_progress: 'bg-accent/15 text-accent',
}

const OUTCOME_ICON: Record<Outcome, string> = {
  success: '✓',
  max: '✗',
  timeout: '✗',
  end_action: '✗',
  fail: '✗',
  abandoned: '–',
  never_connected: '–',
  in_progress: '•',
}

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return (
    <span
      data-outcome={outcome}
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ${OUTCOME_STYLE[outcome]}`}
    >
      <span aria-hidden="true">{OUTCOME_ICON[outcome]}</span>
      {OUTCOME_LABEL[outcome]}
    </span>
  )
}

export function ModeIcon({ mode }: { mode: SessionMode }) {
  const label = mode === 'text' ? 'Text' : 'Voice'
  return (
    <span role="img" aria-label={label} title={label}>
      {mode === 'text' ? '⌨️' : '🎤'}
    </span>
  )
}
