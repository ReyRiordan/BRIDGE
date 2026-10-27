import { useEffect, useState } from 'react'
import Button from '../components/Button'
import { fetchScenario, fetchScenarios } from '../api/scenario'
import type { Scenario, ScenarioSummary } from '../types/scenario'

interface StartScreenProps {
  onLoaded: (scenario: Scenario) => void
}

/**
 * The title card and scenario picker — the only place `/scenarios` and
 * `/scenario/{id}` are fetched. Loading and error state are local: nothing
 * about a failed fetch belongs in game state, and keeping it here is what lets
 * Play Again → Start pick up scenario edits.
 */
function StartScreen({ onLoaded }: StartScreenProps) {
  const [scenarios, setScenarios] = useState<ScenarioSummary[] | null>(null)
  /** The card whose full config is being fetched. */
  const [choosing, setChoosing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  /** What Retry re-runs: the list fetch, or the last card's config fetch. */
  const [failed, setFailed] = useState<'list' | ScenarioSummary | null>(null)

  /** Bumped by a list Retry; the effect refetches on every change. */
  const [listAttempt, setListAttempt] = useState(0)

  useEffect(() => {
    let live = true
    fetchScenarios().then(
      (list) => {
        if (live) setScenarios(list)
      },
      (cause: unknown) => {
        if (!live) return
        setError(
          cause instanceof Error ? cause.message : 'Could not load scenarios.',
        )
        setFailed('list')
      },
    )
    return () => {
      live = false
    }
  }, [listAttempt])

  async function choose(summary: ScenarioSummary) {
    setChoosing(summary.id)
    setError(null)
    setFailed(null)
    try {
      onLoaded(await fetchScenario(summary.id))
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : 'Could not load the scenario.',
      )
      setFailed(summary)
      setChoosing(null)
    }
  }

  function retry() {
    if (failed === 'list') {
      setError(null)
      setFailed(null)
      setListAttempt((n) => n + 1)
    } else if (failed !== null) {
      void choose(failed)
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-10 px-4 py-12 sm:px-6">
      <div className="flex flex-col items-center gap-6 text-center">
        <h1 className="text-5xl font-semibold tracking-[0.32em] text-ink sm:text-7xl">
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
      </div>

      <section aria-labelledby="picker-title" className="w-full max-w-5xl">
        <h2
          id="picker-title"
          className="mb-4 text-center text-xs font-semibold tracking-[0.24em] text-ink-muted uppercase"
        >
          Choose a scenario
        </h2>

        {scenarios === null && error === null && (
          <p className="text-center text-sm text-ink-muted">
            Loading scenarios…
          </p>
        )}

        {scenarios !== null && (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {scenarios.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  data-scenario={s.id}
                  onClick={() => void choose(s)}
                  disabled={choosing !== null}
                  className="group flex h-full w-full cursor-pointer flex-col overflow-hidden rounded-card border border-edge bg-surface text-left transition hover:border-accent focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <img
                    src={`/visuals/${s.id}/${s.intro_image}`}
                    alt=""
                    className="aspect-stage w-full object-cover opacity-80 transition group-hover:opacity-100"
                  />
                  <span className="flex flex-1 items-center justify-between gap-3 p-4">
                    <span className="text-sm font-medium text-ink">
                      {s.title}
                    </span>
                    {choosing === s.id && (
                      <span className="shrink-0 text-xs text-ink-muted">
                        Loading…
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {error && (
          <div
            role="alert"
            className="mt-6 flex flex-col items-center gap-3 text-center"
          >
            <p className="text-sm text-bad">{error}</p>
            <Button variant="ghost" onClick={retry}>
              Retry
            </Button>
          </div>
        )}
      </section>
    </main>
  )
}

export default StartScreen
