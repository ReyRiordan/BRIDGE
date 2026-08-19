import { useEffect } from 'react'
import EscalationBar from '../components/EscalationBar'
import SceneStage from '../components/SceneStage'
import TranscriptPanel from '../components/TranscriptPanel'
import {
  selectAwaitingPatient,
  selectClock,
  selectEscalation,
  selectLayers,
  type GameState,
} from '../state/useGame'

interface GameScreenProps {
  state: GameState
  /** Fired once, 600 ms after game_over lands, so the final frame can register. */
  onGameOverSettled: () => void
}

const END_DELAY_MS = 600

function GameScreen({ state, onGameOverSettled }: GameScreenProps) {
  const escalation = selectEscalation(state)
  const isPlaying = state.phase === 'game'
  const { gameOver } = state

  // Wall-clock lives here, never in the reducer. `onGameOverSettled` must be a
  // stable callback in the parent — an inline arrow would re-run this effect on
  // every render and restart the delay forever.
  useEffect(() => {
    if (!isPlaying || !gameOver) return
    const id = setTimeout(onGameOverSettled, END_DELAY_MS)
    return () => clearTimeout(id)
  }, [isPlaying, gameOver, onGameOverSettled])

  return (
    <main className="mx-auto grid min-h-screen max-w-[1600px] gap-6 p-4 sm:p-6 lg:h-screen lg:grid-cols-[65fr_35fr]">
      <div className="flex min-h-0 flex-col items-center gap-5">
        <SceneStage
          layers={selectLayers(state)}
          clock={selectClock(state)}
          badge={state.lastAction}
        />
        <EscalationBar
          value={escalation.value}
          max={escalation.max}
          pct={escalation.pct}
          tone={escalation.tone}
        />
      </div>

      <TranscriptPanel
        transcript={state.transcript}
        awaitingPatient={selectAwaitingPatient(state)}
      />
    </main>
  )
}

export default GameScreen
