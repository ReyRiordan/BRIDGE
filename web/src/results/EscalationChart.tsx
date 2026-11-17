import { useState } from 'react'
import { mmss, signed } from './format'
import { chartSteps, chipLabel, type StepTone } from './report'
import type { TimelinePoint, TurnRecord } from './types'

interface EscalationChartProps {
  timeline: TimelinePoint[]
  turns: TurnRecord[]
  max: number
  goal: number
  timeLimit: number
  /** Where the line ends (the session's duration); defaults to the last step. */
  endT?: number
}

const W = 640
const H = 240
const PAD = { left: 34, right: 44, top: 14, bottom: 28 }
const PLOT_W = W - PAD.left - PAD.right
const PLOT_H = H - PAD.top - PAD.bottom

// Lookup objects, never interpolation: Tailwind scans source as plain text.
const MARK_CLASS: Record<StepTone, string> = {
  helpful: 'fill-good stroke-surface',
  escalating: 'fill-bad stroke-surface',
  inactivity: 'fill-surface stroke-ink-muted',
}

const TONE_LABEL: Record<StepTone, string> = {
  helpful: 'Helpful turn',
  escalating: 'Escalating turn',
  inactivity: 'Inactivity charge',
}

/**
 * Shape is the second channel beside colour (good/bad sit close under
 * deuteranopia): helpful is a circle, escalating a diamond, inactivity a ring.
 */
function Marker({ tone, x, y }: { tone: StepTone; x: number; y: number }) {
  const cls = MARK_CLASS[tone]
  if (tone === 'escalating') {
    const r = 6
    return (
      <path
        d={`M${x} ${y - r}L${x + r} ${y}L${x} ${y + r}L${x - r} ${y}Z`}
        className={cls}
        strokeWidth={2}
      />
    )
  }
  return (
    <circle
      cx={x}
      cy={y}
      r={tone === 'inactivity' ? 4 : 5}
      className={cls}
      strokeWidth={2}
    />
  )
}

/**
 * The agitation-over-time step chart: hand-rolled SVG (one chart does not earn
 * a chart library). y runs 0 → max (higher is worse), x is the game clock up to
 * the time limit, with dashed goal and max reference lines. Each escalation
 * change carries a marker; hovering or focusing one shows its turn.
 */
function EscalationChart({
  timeline,
  turns,
  max,
  goal,
  timeLimit,
  endT,
}: EscalationChartProps) {
  const [active, setActive] = useState<number | null>(null)
  const limit = Math.max(timeLimit, 1)
  const top = Math.max(max, 1)
  const x = (t: number) =>
    PAD.left + (Math.min(Math.max(t, 0), limit) / limit) * PLOT_W
  const y = (e: number) =>
    PAD.top + (1 - Math.min(Math.max(e, 0), top) / top) * PLOT_H

  const steps = chartSteps(timeline, turns)
  const last = timeline[timeline.length - 1]
  const end = Math.max(endT ?? 0, last?.t ?? 0)

  let path = ''
  timeline.forEach((p, i) => {
    path +=
      i === 0
        ? `M${x(p.t)} ${y(p.escalation)}`
        : `H${x(p.t)}V${y(p.escalation)}`
  })
  if (last) path += `H${x(end)}`

  const yStep = top <= 10 ? 1 : Math.ceil(top / 10)
  const yTicks: number[] = []
  for (let e = 0; e <= top; e += yStep) yTicks.push(e)
  const xStep = limit <= 360 ? 60 : 120
  const xTicks: number[] = []
  for (let t = 0; t <= limit; t += xStep) xTicks.push(t)

  const shown = active !== null ? steps[active] : null

  return (
    <figure className="flex flex-col gap-3">
      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="block h-auto w-full"
          role="img"
          aria-label={`Agitation over time: ${timeline
            .map((p) => `${p.escalation} at ${mmss(p.t)}`)
            .join(', ')}`}
        >
          {yTicks.map((e) => (
            <g key={`y${e}`}>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={y(e)}
                y2={y(e)}
                className="stroke-edge"
                strokeWidth={1}
              />
              <text
                x={PAD.left - 8}
                y={y(e)}
                dy="0.32em"
                textAnchor="end"
                className="fill-ink-muted text-[11px] tabular-nums"
              >
                {e}
              </text>
            </g>
          ))}
          {xTicks.map((t) => (
            <text
              key={`x${t}`}
              x={x(t)}
              y={H - 8}
              textAnchor="middle"
              className="fill-ink-muted text-[11px] tabular-nums"
            >
              {mmss(t)}
            </text>
          ))}

          {[
            { key: 'goal', value: goal, label: 'Goal' },
            { key: 'max', value: top, label: 'Max' },
          ].map((ref) => (
            <g key={ref.key} data-ref={ref.key}>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={y(ref.value)}
                y2={y(ref.value)}
                className="stroke-ink-muted"
                strokeWidth={1}
                strokeDasharray="4 4"
              />
              <text
                x={W - PAD.right + 6}
                y={y(ref.value)}
                dy="0.32em"
                className="fill-ink-muted text-[11px]"
              >
                {ref.label}
              </text>
            </g>
          ))}

          {path && (
            <path
              d={path}
              fill="none"
              className="stroke-accent"
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          )}

          {steps.map((step, i) => {
            const cx = x(step.point.t)
            const cy = y(step.point.escalation)
            return (
              <g
                key={i}
                data-marker
                data-tone={step.tone}
                tabIndex={0}
                aria-label={`${TONE_LABEL[step.tone]} at ${mmss(step.point.t)}: ${step.from} to ${step.point.escalation}`}
                className="cursor-pointer outline-none [&:focus-visible>circle:first-child]:stroke-accent"
                onPointerEnter={() => setActive(i)}
                onPointerLeave={() => setActive((a) => (a === i ? null : a))}
                onFocus={() => setActive(i)}
                onBlur={() => setActive((a) => (a === i ? null : a))}
              >
                {/* The hit target is bigger than the mark. */}
                <circle
                  cx={cx}
                  cy={cy}
                  r={12}
                  fill="transparent"
                  strokeWidth={2}
                />
                <Marker tone={step.tone} x={cx} y={cy} />
              </g>
            )
          })}
        </svg>

        {shown && (
          <div
            role="tooltip"
            className="pointer-events-none absolute z-10 w-64 max-w-[80%] -translate-x-1/2 -translate-y-full rounded-lg border border-edge bg-surface-raised p-3 text-xs shadow-lg"
            style={{
              left: `${(Math.min(Math.max(x(shown.point.t), 120), W - 120) / W) * 100}%`,
              top: `${((y(shown.point.escalation) - 12) / H) * 100}%`,
            }}
          >
            <p className="font-semibold text-ink">
              {TONE_LABEL[shown.tone]} · {mmss(shown.point.t)}
            </p>
            <p className="text-ink-muted tabular-nums">
              Agitation {shown.from} → {shown.point.escalation} (
              {signed(shown.point.escalation - shown.from)})
            </p>
            {shown.turn && shown.turn.actions.length > 0 && (
              <ul className="mt-1.5 flex flex-wrap gap-1">
                {shown.turn.actions.map((a, j) => (
                  <li
                    key={j}
                    className="rounded bg-canvas px-1.5 py-0.5 text-ink"
                  >
                    {chipLabel(a)}
                  </li>
                ))}
              </ul>
            )}
            {shown.turn && (
              <p className="mt-1.5 line-clamp-3 text-ink-muted italic">
                “{shown.turn.content}”
              </p>
            )}
          </div>
        )}
      </div>

      <figcaption className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
        {(['helpful', 'escalating', 'inactivity'] as StepTone[]).map((tone) => (
          <span key={tone} className="flex items-center gap-1.5">
            <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden="true">
              <Marker tone={tone} x={8} y={8} />
            </svg>
            {TONE_LABEL[tone]}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <svg viewBox="0 0 16 4" className="h-1 w-4" aria-hidden="true">
            <line
              x1={0}
              x2={16}
              y1={2}
              y2={2}
              className="stroke-ink-muted"
              strokeDasharray="4 4"
              strokeWidth={2}
            />
          </svg>
          Goal / max
        </span>
      </figcaption>
    </figure>
  )
}

export default EscalationChart
