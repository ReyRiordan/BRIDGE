/**
 * The single import surface for game state: `useGame()` plus a re-export of
 * everything the reducer and selectors define.
 *
 * `dispatch` is also the seam [Rewrite G] plugs the data channel into — wire
 * events are already part of the action union, so the voice client can call
 * `dispatch(JSON.parse(message.data))` directly.
 */
import { useReducer } from 'react'
import { gameReducer, initialState, type GameAction, type GameState } from './gameState'

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
