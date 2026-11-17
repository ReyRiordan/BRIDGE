import { useEffect, useState } from 'react'

interface ActionBadgeProps {
  actionType: string
  desc: string
  pointChange: number
  /** Already credited: a neutral "no points" toast instead of good/bad. */
  repeat: boolean
}

const AUTO_HIDE_MS = 3000

const TONE_CLASS = {
  good: 'bg-good/90 text-canvas',
  bad: 'bg-bad/90 text-canvas',
  neutral: 'bg-surface-raised/90 text-ink-muted',
} as const

/**
 * The "action detected" toast over the scene.
 *
 * Wall-clock lives here rather than in the reducer. The empty dependency array
 * is correct *because* the parent keys this component by `lastAction.id`: a
 * repeat detection remounts it, which restarts the timer and replays the pop
 * animation — something the legacy single-element badge could not do.
 *
 * A repeat (a helpful action the session already credited) gets a neutral
 * tone and says so, so the student learns why the bar did not move.
 */
function ActionBadge({
  actionType,
  desc,
  pointChange,
  repeat,
}: ActionBadgeProps) {
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    const id = setTimeout(() => setVisible(false), AUTO_HIDE_MS)
    return () => clearTimeout(id)
  }, [])

  if (!visible) return null

  const tone = repeat ? 'neutral' : pointChange < 0 ? 'good' : 'bad'
  return (
    <div
      role="status"
      aria-live="polite"
      data-tone={tone}
      className={`absolute bottom-5 left-1/2 z-50 max-w-[85%] -translate-x-1/2 rounded-full px-5 py-2 text-center text-sm font-medium shadow-lg backdrop-blur-sm motion-safe:animate-[badge-in_0.28s_ease-out] ${TONE_CLASS[tone]}`}
    >
      {repeat ? `${actionType} — already credited` : `${actionType}: ${desc}`}
    </div>
  )
}

export default ActionBadge
