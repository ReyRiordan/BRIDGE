import { useEffect, useState } from 'react'

interface ActionBadgeProps {
  actionType: string
  desc: string
  pointChange: number
}

const AUTO_HIDE_MS = 3000

/**
 * The "action detected" toast over the scene.
 *
 * Wall-clock lives here rather than in the reducer. The empty dependency array
 * is correct *because* the parent keys this component by `lastAction.id`: a
 * repeat detection remounts it, which restarts the timer and replays the pop
 * animation — something the legacy single-element badge could not do.
 */
function ActionBadge({ actionType, desc, pointChange }: ActionBadgeProps) {
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    const id = setTimeout(() => setVisible(false), AUTO_HIDE_MS)
    return () => clearTimeout(id)
  }, [])

  if (!visible) return null

  const good = pointChange < 0
  return (
    <div
      role="status"
      aria-live="polite"
      data-tone={good ? 'good' : 'bad'}
      className={`absolute bottom-5 left-1/2 z-50 max-w-[85%] -translate-x-1/2 rounded-full px-5 py-2 text-center text-sm font-medium shadow-lg backdrop-blur-sm motion-safe:animate-[badge-in_0.28s_ease-out] ${
        good ? 'bg-good/90 text-canvas' : 'bg-bad/90 text-canvas'
      }`}
    >
      {actionType}: {desc}
    </div>
  )
}

export default ActionBadge
