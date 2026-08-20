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
  /** From `useVoiceSession` — drives the read-only mic pill over the scene. */
  isAgentSpeaking?: boolean
  isMuted?: boolean
}

/**
 * Pure presentation. The handoff to the end screen is NOT timed here: it waits
 * on the final patient audio, which only the voice session can see (see
 * `voice/useVoiceSession.ts`).
 */
function GameScreen({
  state,
  isAgentSpeaking = false,
  isMuted = false,
}: GameScreenProps) {
  const escalation = selectEscalation(state)

  return (
    <main className="mx-auto grid min-h-screen max-w-[1600px] gap-6 p-4 sm:p-6 lg:h-screen lg:grid-cols-[65fr_35fr]">
      <div className="flex min-h-0 flex-col items-center gap-5">
        <SceneStage
          layers={selectLayers(state)}
          clock={selectClock(state)}
          badge={state.lastAction}
          mic={{ agentSpeaking: isAgentSpeaking, muted: isMuted }}
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
