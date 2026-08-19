/**
 * Session lifecycle: one game = one voice session.
 *
 * This is the only place effectful session state lives — session ids, the
 * connecting/retry dance, the end settle, the connection-lost flag. The game
 * reducer stays pure (no wall clock, no ids), and `App` composes this hook's
 * status against `state.phase` to decide what renders.
 *
 * Two ids, two jobs:
 *  - `session_id` — the domain key, minted here with `crypto.randomUUID()` and
 *    stable for the whole game. The control plane treats it as a free-form
 *    path key.
 *  - `runtime_session_id` — the AgentCore container-affinity key. Freshly
 *    minted by `/start` on EVERY connect attempt; reusing a stale one pins
 *    signaling to a container that may be gone.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { RELAY_ONLY } from '../config'
import type { GameAction, GameState } from '../state/useGame'
import { createGameEventHandler } from './gameEvents'
import { useWebRTC } from './useWebRTC'
import { endVoiceSession, startVoiceSession } from './voiceApi'
import type { WebRTCError } from './webrtc.types'

/**
 * How long the final patient audio gets to START after `game_over`. The event
 * arrives BEFORE the closing `transcript_update` and before TTS begins, so
 * waiting for `isAgentSpeaking === false` naively would settle instantly. When
 * there is no closing line at all (a timeout fail), this window IS the settle.
 */
const AUDIO_RISE_WINDOW_MS = 3000

/**
 * Backstop if the falling edge never comes. Must stay well inside the
 * runtime's `GAME_GRACE_SECONDS` (45 s) — after that the reaper kills the
 * pipeline and the audio dies mid-line anyway.
 */
const SETTLE_HARD_CAP_MS = 20000

export type VoiceSessionStatus = 'idle' | 'connecting' | 'active' | 'ended'

export interface UseVoiceSession {
  /** Mic permission → `/start` → connect. Dispatches BEGIN once ICE is up. */
  begin: () => Promise<void>
  /** Play Again: drop ids, flags and timers so the next `begin()` is a new session. */
  reset: () => void
  status: VoiceSessionStatus
  /** Surfaced inline on the intro screen; the user retries by hand. */
  error: WebRTCError | null
  /** A mid-game drop. Not a game `fail` — the UI says so in its own words. */
  connectionLost: boolean
  isAgentSpeaking: boolean
  isMuted: boolean
}

/** Normalize anything thrown by the transport into the WebRTCError surface. */
function toWebRTCError(err: unknown): WebRTCError {
  const candidate = err as Partial<WebRTCError> | null
  if (candidate && typeof candidate.code === 'string') {
    return { code: candidate.code, message: candidate.message ?? '' }
  }
  return {
    code: 'CONNECTION_FAILED',
    message:
      err instanceof Error ? err.message : 'Could not reach the simulation.',
  }
}

export function useVoiceSession(
  state: GameState,
  dispatch: React.Dispatch<GameAction>,
): UseVoiceSession {
  const [status, setStatus] = useState<VoiceSessionStatus>('idle')
  const [error, setError] = useState<WebRTCError | null>(null)
  const [connectionLost, setConnectionLost] = useState(false)

  const sessionIdRef = useRef<string | null>(null)
  const runtimeSessionIdRef = useRef<string | null>(null)
  /** `/end` fires at most once per session, whichever path gets there first. */
  const endedOnceRef = useRef(false)
  /**
   * `useWebRTC`'s 1 s poll flips `callState` to 'ended' on a drop AND our own
   * `endCall()` does the same — this ref is what tells the two apart.
   */
  const intentionalEndRef = useRef(false)
  const beginningRef = useRef(false)
  const settleRef = useRef<'idle' | 'rise' | 'fall' | 'done'>('idle')
  const riseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const capTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const onGameEventRef = useRef(createGameEventHandler(dispatch))
  useEffect(() => {
    onGameEventRef.current = createGameEventHandler(dispatch)
  }, [dispatch])

  const {
    callState,
    isAgentSpeaking,
    isMuted,
    requestPermission,
    startCall,
    endCall,
  } = useWebRTC({ onGameEvent: (event) => onGameEventRef.current(event) })

  const clearTimers = useCallback(() => {
    if (riseTimerRef.current) clearTimeout(riseTimerRef.current)
    if (capTimerRef.current) clearTimeout(capTimerRef.current)
    riseTimerRef.current = null
    capTimerRef.current = null
  }, [])

  /** Best-effort runtime teardown, guarded so it runs exactly once. */
  const endSessionOnce = useCallback(async () => {
    const sessionId = sessionIdRef.current
    const runtimeSessionId = runtimeSessionIdRef.current
    if (endedOnceRef.current || !sessionId || !runtimeSessionId) return
    endedOnceRef.current = true
    try {
      await endVoiceSession(sessionId, { runtime_session_id: runtimeSessionId })
    } catch {
      // /end is best-effort by design: the runtime's idle timeout is the backstop.
    }
  }, [])

  const begin = useCallback(async () => {
    if (beginningRef.current) return
    beginningRef.current = true
    setError(null)
    setStatus('connecting')

    try {
      // Inside the click gesture, so the browser actually shows the prompt.
      await requestPermission()
    } catch (err) {
      setError(toWebRTCError(err))
      setStatus('idle')
      beginningRef.current = false
      return
    }

    const sessionId = crypto.randomUUID()
    sessionIdRef.current = sessionId
    endedOnceRef.current = false
    intentionalEndRef.current = false

    // One silent retry, and only for an ICE stall: a cold-started container
    // can fail to produce a relay path, and a fresh /start (new
    // runtime_session_id, same session_id) is the sanctioned recovery. A
    // server-side failure is not retried — a second attempt fails the same way.
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const { runtime_session_id, ice_servers } =
          await startVoiceSession(sessionId)
        runtimeSessionIdRef.current = runtime_session_id
        await startCall(sessionId, runtime_session_id, ice_servers, RELAY_ONLY)
        setStatus('active')
        dispatch({ type: 'BEGIN' })
        beginningRef.current = false
        return
      } catch (err) {
        const wrapped = toWebRTCError(err)
        if (wrapped.code === 'ICE_STALL' && attempt === 0) continue
        setError(wrapped)
        setStatus('idle')
        beginningRef.current = false
        return
      }
    }
  }, [dispatch, requestPermission, startCall])

  /** Tear down locally, tell the runtime, then hand the end screen over. */
  const settle = useCallback(async () => {
    if (settleRef.current === 'done') return
    settleRef.current = 'done'
    clearTimers()
    intentionalEndRef.current = true
    try {
      await endCall()
    } catch {
      // Already down; the end screen does not depend on a clean teardown.
    }
    await endSessionOnce()
    setStatus('ended')
    dispatch({ type: 'SHOW_END' })
  }, [clearTimers, dispatch, endCall, endSessionOnce])

  // End settle: rising-then-falling audio edge (see AUDIO_RISE_WINDOW_MS).
  useEffect(() => {
    if (state.phase !== 'game' || state.gameOver === null) return
    if (settleRef.current === 'done') return

    // A drop after game_over is a settle, not a connection loss: the audio is
    // dead either way, so the student still gets their normal debrief.
    if (callState === 'ended') {
      void settle()
      return
    }

    if (settleRef.current === 'idle') {
      settleRef.current = 'rise'
      riseTimerRef.current = setTimeout(() => {
        if (settleRef.current === 'rise') void settle()
      }, AUDIO_RISE_WINDOW_MS)
      capTimerRef.current = setTimeout(() => void settle(), SETTLE_HARD_CAP_MS)
    }

    if (settleRef.current === 'rise' && isAgentSpeaking) {
      settleRef.current = 'fall'
      if (riseTimerRef.current) clearTimeout(riseTimerRef.current)
      riseTimerRef.current = null
    } else if (settleRef.current === 'fall' && !isAgentSpeaking) {
      void settle()
    }
  }, [state.phase, state.gameOver, isAgentSpeaking, callState, settle])

  // Drop watcher. Mid-game, a reconnect can land on a cold container at reset
  // escalation, so there is no automatic retry after BEGIN — the loss is shown.
  useEffect(() => {
    if (callState !== 'ended' || intentionalEndRef.current) return
    if (state.phase !== 'game' || state.gameOver !== null) return

    intentionalEndRef.current = true
    setConnectionLost(true)
    setStatus('ended')
    clearTimers()
    settleRef.current = 'done'
    void (async () => {
      try {
        await endCall()
      } catch {
        // nothing left to close
      }
      await endSessionOnce()
    })()
  }, [
    callState,
    state.phase,
    state.gameOver,
    clearTimers,
    endCall,
    endSessionOnce,
  ])

  const reset = useCallback(() => {
    clearTimers()
    sessionIdRef.current = null
    runtimeSessionIdRef.current = null
    endedOnceRef.current = false
    intentionalEndRef.current = false
    beginningRef.current = false
    settleRef.current = 'idle'
    setStatus('idle')
    setError(null)
    setConnectionLost(false)
  }, [clearTimers])

  // Abandonment (tab close, navigate away): no pagehide beacon — the runtime's
  // idle timeout and session sweep are the designed backstop.
  useEffect(() => {
    return () => {
      clearTimers()
      intentionalEndRef.current = true
      void endCall()
      void endSessionOnce()
    }
  }, [clearTimers, endCall, endSessionOnce])

  return {
    begin,
    reset,
    status,
    error,
    connectionLost,
    isAgentSpeaking,
    isMuted,
  }
}
