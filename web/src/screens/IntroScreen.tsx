import Button from '../components/Button'
import type { Scenario } from '../types/scenario'

interface IntroScreenProps {
  scenario: Scenario
  onBegin: () => void
  /** A connect is in flight: AgentCore's cold start can take several seconds. */
  connecting?: boolean
  /** Mic denial or an exhausted connect — the user retries with the button. */
  error?: string | null
}

/**
 * Also the connect screen: a failed connect leaves the student here with an
 * error and a Retry rather than on a dead game screen, because BEGIN is only
 * dispatched once ICE is actually up.
 */
function IntroScreen({
  scenario,
  onBegin,
  connecting = false,
  error = null,
}: IntroScreenProps) {
  return (
    <main className="mx-auto grid min-h-screen max-w-6xl items-center gap-10 px-6 py-12 md:grid-cols-2">
      <img
        src="/visuals/intro.jpg"
        alt="The emergency department room where the encounter takes place"
        className="w-full rounded-card border border-edge object-cover shadow-2xl"
      />

      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-3">
          <h1 className="text-xs font-semibold tracking-[0.3em] text-accent uppercase">
            Case Introduction
          </h1>
          <p className="text-base leading-relaxed text-ink">{scenario.intro}</p>
        </div>

        <div className="rounded-card border-l-4 border-accent bg-surface p-5">
          <h2 className="mb-2 text-xs font-semibold tracking-[0.24em] text-accent uppercase">
            Your Goal
          </h2>
          <p className="text-sm leading-relaxed text-ink-muted">
            {scenario.goal}
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <Button
            variant="success"
            onClick={onBegin}
            disabled={connecting}
            className="self-start"
          >
            {connecting ? 'Connecting…' : error ? 'Retry' : 'Begin Simulation'}
          </Button>

          {connecting && (
            <p className="text-sm text-ink-muted">
              Connecting to the patient — this can take a few seconds.
            </p>
          )}

          {!connecting && error && (
            <p role="alert" className="text-sm text-bad">
              {error}
            </p>
          )}
        </div>
      </div>
    </main>
  )
}

export default IntroScreen
