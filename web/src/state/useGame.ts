/**
 * The single import surface for game state: `useGame()` plus a re-export of
 * everything the reducer and selectors define.
 *
 * `dispatch` is also the seam the data channel plugs into — wire events are
 * already part of the action union, so the voice client's
 * `createGameEventHandler(dispatch)` (registered by `useVoiceSession`) needs no
 * adapter.
 */
import { useReducer } from 'react'
import {
  gameReducer,
  initialState,
  type GameAction,
  type GameState,
} from './gameState'

export * from './gameState'
export * from './selectors'

export interface UseGame {
  state: GameState
  dispatch: React.Dispatch<GameAction>
}

export function useGame(): UseGame {
  const [state, dispatch] = useReducer(gameReducer, initialState)
  return { state, dispatch }
}
