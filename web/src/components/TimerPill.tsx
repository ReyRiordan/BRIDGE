interface TimerPillProps {
  text: string
  urgent: boolean
}

/**
 * Stateless by design. The runtime owns the clock and is authoritative for
 * expiry — the UI never counts down on its own and never ends the game itself.
 */
function TimerPill({ text, urgent }: TimerPillProps) {
  return (
    <div
      role="timer"
      aria-label="Time remaining"
      className={`absolute top-4 right-4 z-50 rounded-full px-4 py-1.5 font-mono text-lg font-semibold tabular-nums backdrop-blur-sm ${
        urgent
          ? 'bg-esc-crit/85 text-white motion-safe:animate-pulse'
          : 'bg-canvas/70 text-ink'
      }`}
    >
      {text}
    </div>
  )
}

export default TimerPill
