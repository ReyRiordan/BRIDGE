import { useCallback } from 'react'
import EndScreen, { type EndOutcome } from './screens/EndScreen'
import GameScreen from './screens/GameScreen'
import IntroScreen from './screens/IntroScreen'
import StartScreen from './screens/StartScreen'
import { selectChecklist, useGame } from './state/useGame'
import type { Scenario } from './types/scenario'
import { useVoiceSession } from './voice/useVoiceSession'

/** Friendly copy for the codes the session can surface on the intro screen. */
const ERROR_COPY: Record<string, string> = {
  MICROPHONE_PERMISSION_DENIED:
    'Microphone access is required — allow it in your browser, then try again.',
  ICE_STALL: 'Could not reach the patient. Please try again.',
}

const CONNECTION_LOST: EndOutcome = {
  status: 'connection_lost',
  reason: 'Connection lost — the session could not continue.',
}

/**
 * The whole app shell: one reducer, one switch on `state.phase`, and one voice
 * session hung off it.
 *
 * `dispatch` is the seam the voice data channel plugs into — wire events are
 * already part of the action union, so a parsed message goes straight in with
 * no adapter. Everything effectful about the session (ids, connect, settle,
 * drops) lives in `useVoiceSession`; this component only composes its status
 * with the game phase to decide what renders.
 */
function App() {
  const { state, dispatch } = useGame()
  const voice = useVoiceSession(state, dispatch)

  const onLoaded = useCallback(
    (scenario: Scenario) => dispatch({ type: 'SCENARIO_LOADED', scenario }),
    [dispatch],
  )

  const { reset } = voice
  const onPlayAgain = useCallback(() => {
    reset()
    dispatch({ type: 'PLAY_AGAIN' })
  }, [dispatch, reset])

  if (state.phase === 'start' || state.scenario === null) {
    return <StartScreen onLoaded={onLoaded} />
  }

  if (state.phase === 'intro') {
    return (
      <IntroScreen
        scenario={state.scenario}
        onBegin={() => void voice.begin()}
        connecting={voice.status === 'connecting'}
        error={
          voice.error
            ? (ERROR_COPY[voice.error.code] ?? voice.error.message)
            : null
        }
      />
    )
  }

  // A drop is its own outcome: it overlays the same debrief, but never wearing
  // the game's `fail` copy.
  const outcome: EndOutcome | null = voice.connectionLost
    ? CONNECTION_LOST
    : state.phase === 'end' && state.gameOver
      ? state.gameOver
      : null

  return (
    <>
      {/* The scene stays mounted under the debrief overlay. */}
      <GameScreen
        state={state}
        isAgentSpeaking={voice.isAgentSpeaking}
        isMuted={voice.isMuted}
      />
      {outcome && (
        <EndScreen
          gameOver={outcome}
          checklist={selectChecklist(state)}
          onPlayAgain={onPlayAgain}
        />
      )}
    </>
  )
}

export default App
