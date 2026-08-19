import type { EscTone } from '../state/useGame'

interface EscalationBarProps {
  value: number
  max: number
  pct: number
  tone: EscTone
}

// Lookup, never interpolation — see the note in index.css.
const FILL: Record<EscTone, string> = {
  calm: 'bg-esc-calm',
  watch: 'bg-esc-watch',
  warn: 'bg-esc-warn',
  crit: 'bg-esc-crit',
}

const TEXT: Record<EscTone, string> = {
  calm: 'text-esc-calm',
  watch: 'text-esc-watch',
  warn: 'text-esc-warn',
  crit: 'text-esc-crit',
}

/** Inverted on purpose: this fills toward danger, so empty is the win state. */
function EscalationBar({ value, max, pct, tone }: EscalationBarProps) {
  return (
    <div className="w-full max-w-4xl">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs font-semibold tracking-[0.18em] text-ink-muted uppercase">
          Escalation Level
        </span>
        <span
          className={`font-mono text-sm font-semibold tabular-nums ${TEXT[tone]}`}
        >
          {value} / {max}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Escalation level"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
        data-tone={tone}
        className="h-3 w-full overflow-hidden rounded-full bg-surface-raised ring-1 ring-edge ring-inset"
      >
        <div
          className={`h-full rounded-full transition-[width,background-color] duration-500 ease-out ${FILL[tone]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default EscalationBar
