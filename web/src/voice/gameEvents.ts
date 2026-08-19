/**
 * The data channel → reducer seam.
 *
 * The service hands over whatever it parsed off the channel; this handler only
 * checks that it is a plain object (so the cast below is honest) and dispatches
 * it. Envelope validation — unknown `type`, `v !== 1` — lives in the reducer's
 * `isAcceptable` and nowhere else, so the two can never drift apart.
 */
import type { Dispatch } from 'react'
import type { GameAction } from '../state/gameState'

/** Build the `onGameEvent` callback the WebRTC service calls per message. */
export function createGameEventHandler(
  dispatch: Dispatch<GameAction>,
): (event: unknown) => void {
  return (event: unknown) => {
    if (typeof event !== 'object' || event === null || Array.isArray(event)) {
      return
    }
    dispatch(event as GameAction)
  }
}
