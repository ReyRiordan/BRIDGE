import { useCallback, useEffect, useState } from 'react'
import EscalationBar from '../components/EscalationBar'
import PatientMuteToggle from '../components/PatientMuteToggle'
import SceneStage from '../components/SceneStage'
import TextComposer from '../components/TextComposer'
import TranscriptPanel from '../components/TranscriptPanel'
import type { Mode } from '../identity'
import {
  selectAwaitingPatient,
  selectClock,
  selectEscalation,
  selectLayers,
  type GameState,
} from '../state/useGame'

/**
 * How long a typed message holds the composer if no patient reply comes. The
 * referee can time out, and a turn can produce no reply, so this is the
 * backstop that keeps the composer from locking for good.
 */
export const AWAITING_REPLY_FALLBACK_MS = 15000

interface GameScreenProps {
  state: GameState
  /** Voice (mic pill) or Text (composer + patient mute). */
  mode?: Mode
  /** From `useVoiceSession` — drives the read-only mic pill over the scene. */
  isAgentSpeaking?: boolean
  isMuted?: boolean
  /** Text mode: send one typed turn; false when it was not sent. */
  onSendText?: (content: string) => boolean
  patientMuted?: boolean
  onTogglePatientMuted?: () => void
}

/**
 * Presentation, plus Text mode's one-message-in-flight rule. The handoff to
 * the end screen is NOT timed here: it waits on the final patient audio, which
 * only the voice session can see (see `voice/useVoiceSession.ts`).
 */
function GameScreen({
  state,
  mode = 'voice',
  isAgentSpeaking = false,
  isMuted = false,
  onSendText,
  patientMuted = false,
  onTogglePatientMuted,
}: GameScreenProps) {
  const escalation = selectEscalation(state)
  const text = mode === 'text'

  // One message in flight: a send records how many patient turns there were,
  // and the composer stays locked until that count moves, the game ends, or
  // the fallback fires. Derived from the transcript, so no event is missed.
  const patientTurns = state.transcript.filter(
    (entry) => entry.role === 'patient',
  ).length
  const [sent, setSent] = useState<{
    seq: number
    patientTurns: number
  } | null>(null)
  const awaitingReply =
    sent !== null &&
    sent.patientTurns === patientTurns &&
    state.gameOver === null

  useEffect(() => {
    if (sent === null) return
    const timer = setTimeout(
      () => setSent((now) => (now?.seq === sent.seq ? null : now)),
      AWAITING_REPLY_FALLBACK_MS,
    )
    return () => clearTimeout(timer)
  }, [sent])

  const onSend = useCallback(
    (content: string) => {
      if (!onSendText?.(content)) return false
      setSent((prev) => ({ seq: (prev?.seq ?? 0) + 1, patientTurns }))
      return true
    },
    [onSendText, patientTurns],
  )

  return (
    <main className="mx-auto grid min-h-screen max-w-[1600px] gap-6 p-4 sm:p-6 lg:h-screen lg:grid-cols-[65fr_35fr]">
      <div className="flex min-h-0 flex-col items-center gap-5">
        <SceneStage
          scenario={state.scenario}
          layers={selectLayers(state)}
          clock={selectClock(state)}
          badge={state.lastAction}
          mic={
            text
              ? undefined
              : { agentSpeaking: isAgentSpeaking, muted: isMuted }
          }
          corner={
            text ? (
              <PatientMuteToggle
                muted={patientMuted}
                onToggle={() => onTogglePatientMuted?.()}
              />
            ) : undefined
          }
        />
        <EscalationBar
          value={escalation.value}
          max={escalation.max}
          pct={escalation.pct}
          tone={escalation.tone}
        />
      </div>

      {/* The transcript fills the column; the composer sits under it. */}
      <div className="flex min-h-0 flex-col gap-4 [&>section]:flex-1">
        <TranscriptPanel
          transcript={state.transcript}
          awaitingPatient={selectAwaitingPatient(state)}
          emptyHint={text ? 'Type below to begin the conversation.' : undefined}
        />
        {text && (
          <TextComposer
            onSend={onSend}
            awaitingReply={awaitingReply}
            disabled={state.gameOver !== null}
          />
        )}
      </div>
    </main>
  )
}

export default GameScreen
