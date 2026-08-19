import ActionChecklist from '../components/ActionChecklist'
import Button from '../components/Button'
import type { ChecklistRow, GameOverState } from '../state/useGame'

interface EndScreenProps {
  gameOver: GameOverState
  checklist: ChecklistRow[]
  onPlayAgain: () => void
}

/**
 * A scrim + card over the frozen final scene rather than a separate route: the
 * debrief reads better with the state that produced it still visible behind it.
 */
function EndScreen({ gameOver, checklist, onPlayAgain }: EndScreenProps) {
  const success = gameOver.status === 'success'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="end-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/85 p-4 backdrop-blur-sm"
    >
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-card border border-edge bg-surface shadow-2xl">
        <div className="border-b border-edge px-7 py-6 text-center">
          <h2
            id="end-title"
            className={`text-2xl font-semibold ${success ? 'text-good' : 'text-bad'}`}
          >
            {success ? 'De-escalation Successful!' : 'Simulation Ended'}
          </h2>
          <p className="mt-2 text-sm text-ink-muted">{gameOver.reason}</p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-7 py-5">
          <h3 className="mb-2 text-xs font-semibold tracking-[0.24em] text-ink-muted uppercase">
            Actions — Found vs. Missed
          </h3>
          <ActionChecklist rows={checklist} />
        </div>

        <div className="border-t border-edge px-7 py-5 text-center">
          <Button autoFocus onClick={onPlayAgain}>
            Play Again
          </Button>
        </div>
      </div>
    </div>
  )
}

export default EndScreen
