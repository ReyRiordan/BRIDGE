import { useCallback } from 'react'
import EndScreen from './screens/EndScreen'
import GameScreen from './screens/GameScreen'
import IntroScreen from './screens/IntroScreen'
import StartScreen from './screens/StartScreen'
import { selectChecklist, useGame } from './state/useGame'
import type { Scenario } from './types/scenario'

/**
 * The whole app shell: one reducer, one switch on `state.phase`.
 *
 * `dispatch` is also the seam the voice data channel plugs into — wire events
 * are already part of the action union, so a parsed message goes straight in
 * with no adapter. [Rewrite G2] mounts the session on the game screen.
 */
function App() {
  const { state, dispatch } = useGame()

  const onLoaded = useCallback(
    (scenario: Scenario) => dispatch({ type: 'SCENARIO_LOADED', scenario }),
    [dispatch],
  )
  // Stable by necessity: GameScreen's 600 ms end delay depends on it, and an
  // inline arrow would restart that timer on every render.
  const onGameOverSettled = useCallback(
    () => dispatch({ type: 'SHOW_END' }),
    [dispatch],
  )

  if (state.phase === 'start' || state.scenario === null) {
    return <StartScreen onLoaded={onLoaded} />
  }

  if (state.phase === 'intro') {
    return (
      <IntroScreen
        scenario={state.scenario}
        onBegin={() => dispatch({ type: 'BEGIN' })}
      />
    )
  }

  return (
    <>
      {/* The scene stays mounted under the debrief overlay. */}
      <GameScreen state={state} onGameOverSettled={onGameOverSettled} />
      {state.phase === 'end' && state.gameOver && (
        <EndScreen
          gameOver={state.gameOver}
          checklist={selectChecklist(state)}
          onPlayAgain={() => dispatch({ type: 'PLAY_AGAIN' })}
        />
      )}
    </>
  )
}

export default App
