import { useState } from 'react'
import Button from '../components/Button'
import { fetchScenario } from '../api/scenario'
import type { Scenario } from '../types/scenario'

interface StartScreenProps {
  onLoaded: (scenario: Scenario) => void
}

/**
 * The title card, and the only place `/scenario` is fetched. Loading and error
 * state are local: nothing about a failed fetch belongs in game state, and
 * keeping it here is what lets Play Again → Start pick up scenario edits.
 */
function StartScreen({ onLoaded }: StartScreenProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function start() {
    setLoading(true)
    setError(null)
    try {
      onLoaded(await fetchScenario())
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : 'Could not load the scenario.',
      )
      setLoading(false)
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden px-6">
      <img
        src="/visuals/background.jpg"
        alt=""
        aria-hidden="true"
        className="absolute inset-0 h-full w-full object-cover opacity-25"
      />
      <div className="absolute inset-0 bg-gradient-to-b from-canvas/70 via-canvas/85 to-canvas" />

      <div className="relative flex flex-col items-center gap-6 text-center">
        <h1 className="text-6xl font-semibold tracking-[0.32em] text-ink sm:text-7xl">
          BRIDGE
        </h1>
        <p className="text-sm font-semibold tracking-[0.3em] text-accent uppercase">
          De-escalation Simulation
        </p>
        <p className="max-w-md text-sm leading-relaxed text-ink-muted">
          Behavioral response and interactive de-escalation training for medical
          students. Speak with the patient, read the room, and bring the
          escalation down before time runs out.
        </p>

        <Button onClick={start} disabled={loading} className="mt-2">
          {loading ? 'Loading…' : 'Start'}
        </Button>

        {error && (
          <div role="alert" className="flex flex-col items-center gap-3">
            <p className="text-sm text-bad">{error}</p>
            <Button variant="ghost" onClick={start}>
              Retry
            </Button>
          </div>
        )}
      </div>
    </main>
  )
}

export default StartScreen
