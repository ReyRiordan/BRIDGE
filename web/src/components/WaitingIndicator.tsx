function WaitingIndicator() {
  return (
    <div
      data-testid="waiting-indicator"
      className="flex items-center gap-2 px-1 text-xs text-ink-muted italic"
    >
      <span className="flex gap-1" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-ink-muted motion-safe:animate-dot-pulse"
            style={{ animationDelay: `${i * 0.16}s` }}
          />
        ))}
      </span>
      Waiting for patient response…
    </div>
  )
}

export default WaitingIndicator
