/**
 * useWebRTC Hook
 * React hook wrapper for the WebRTC voice service with lifecycle management.
 * Connects to the Pipecat voice runtime via the `/signal` proxy, forwards the
 * game events that arrive over the data channel to the caller's handler, and
 * detects a cold-start ICE stall so the caller can retry with a fresh
 * runtime_session_id (see docs/frontend/voice-client.md for the retry loop).
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { webrtcService } from './webrtc.service'
import type { CallState, IceServerConfig, WebRTCError } from './webrtc.types'

// How long to wait for ICE to reach `connected` before declaring a cold-start
// stall and tearing down so the caller can retry with a fresh runtime_session_id.
const ICE_CONNECT_TIMEOUT_MS = 10000

/** Options for the hook. */
export interface UseWebRTCOptions {
  /**
   * Called with every JSON payload parsed off the data channel. Pass
   * `createGameEventHandler(dispatch)` (./gameEvents) — the reducer is the
   * single validation point, so nothing is filtered on the way here.
   */
  onGameEvent?: (event: unknown) => void
}

/**
 * useWebRTC hook return type
 */
export interface UseWebRTCReturn {
  callState: CallState
  connectionState: RTCPeerConnectionState | null
  isMuted: boolean
  isAgentSpeaking: boolean
  error: WebRTCError | null
  requestPermission: () => Promise<void>
  startCall: (
    sessionId: string,
    runtimeSessionId: string,
    iceServers?: IceServerConfig[],
    relayOnly?: boolean,
  ) => Promise<() => void>
  endCall: () => Promise<void>
  toggleMute: () => void
}

/**
 * useWebRTC Hook
 * Manages the WebRTC connection lifecycle; self-contained (call state lives in
 * the hook — surface it to your own UI/context as needed).
 *
 * Example usage:
 * ```tsx
 * const { startCall, endCall } = useWebRTC({
 *   onGameEvent: createGameEventHandler(dispatch),
 * });
 * await requestPermission();
 * await startCall(sessionId, runtimeSessionId, iceServers, RELAY_ONLY);
 * ```
 */
export const useWebRTC = (options: UseWebRTCOptions = {}): UseWebRTCReturn => {
  const [callState, setCallState] = useState<CallState>('idle')
  const [connectionState, setConnectionState] =
    useState<RTCPeerConnectionState | null>(null)
  const [isMuted, setIsMuted] = useState(false)
  const [isAgentSpeaking, setIsAgentSpeaking] = useState(false)
  const [error, setError] = useState<WebRTCError | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Read through a ref so an inline callback doesn't re-register (and briefly
  // drop) the service handler on every render.
  const onGameEventRef = useRef(options.onGameEvent)
  useEffect(() => {
    onGameEventRef.current = options.onGameEvent
  }, [options.onGameEvent])

  // Register remote audio + game-event callbacks; clear on unmount
  useEffect(() => {
    webrtcService.onRemoteAudioStateChange = setIsAgentSpeaking
    webrtcService.onGameEvent = (event) => onGameEventRef.current?.(event)
    return () => {
      webrtcService.onRemoteAudioStateChange = null
      webrtcService.onGameEvent = null
    }
  }, [])

  // Mute the local track while the agent is speaking to prevent acoustic
  // echo from triggering the backend VAD. When the agent stops, restore the
  // user's own mute preference.
  useEffect(() => {
    webrtcService.setMuted(isAgentSpeaking || isMuted)
  }, [isAgentSpeaking, isMuted])

  /**
   * Request microphone permission
   */
  const requestPermission = useCallback(async () => {
    try {
      setError(null)
      await webrtcService.requestMicrophonePermission()
    } catch (err) {
      const webrtcError = err as WebRTCError
      setError(webrtcError)
      throw webrtcError
    }
  }, [])

  /**
   * Start a WebRTC call.
   *
   * Establishes the connection via the `/signal` proxy, then waits up to
   * ICE_CONNECT_TIMEOUT_MS for ICE to reach `connected`. If ICE stalls (cold
   * start), it tears down and throws an ICE_STALL error so the caller can
   * retry with a fresh runtime_session_id.
   *
   * @param sessionId - host session id
   * @param runtimeSessionId - AgentCore affinity key from the start endpoint
   * @param iceServers - the browser's own KVS managed-TURN servers (relay-only)
   * @param relayOnly - defaults to true; callers pass `RELAY_ONLY` from
   *   src/config.ts, which is false only under VITE_BRIDGE_LOCAL=1
   * @returns cleanup fn that clears the connection-state poll
   */
  const startCall = useCallback(
    async (
      sessionId: string,
      runtimeSessionId: string,
      iceServers?: IceServerConfig[],
      relayOnly: boolean = true,
    ) => {
      try {
        setError(null)
        setCallState('connecting')

        await webrtcService.initializeConnection(
          sessionId,
          runtimeSessionId,
          iceServers,
          relayOnly,
        )

        // Wait for ICE to actually connect; a cold-started runtime can fail to
        // produce a usable relay path, in which case we surface an ICE_STALL.
        await new Promise<void>((resolve, reject) => {
          const start = Date.now()
          const check = setInterval(() => {
            const ice = webrtcService.getIceConnectionState()
            if (ice === 'connected' || ice === 'completed') {
              clearInterval(check)
              resolve()
            } else if (
              ice === 'failed' ||
              Date.now() - start >= ICE_CONNECT_TIMEOUT_MS
            ) {
              clearInterval(check)
              reject(new Error('ICE connection stalled'))
            }
          }, 250)
        }).catch(async (e) => {
          await webrtcService.closeConnection()
          const stallError: WebRTCError = {
            code: 'ICE_STALL',
            message: e.message,
          }
          throw stallError
        })

        setCallState('connected')
        setConnectionState(webrtcService.getConnectionState())

        // Poll connection state to detect drops
        if (pollRef.current) clearInterval(pollRef.current)
        const interval = setInterval(() => {
          const state = webrtcService.getConnectionState()
          setConnectionState(state)

          if (
            state === 'failed' ||
            state === 'disconnected' ||
            state === 'closed'
          ) {
            clearInterval(interval)
            setCallState('ended')
          }
        }, 1000)
        pollRef.current = interval

        return () => clearInterval(interval)
      } catch (err) {
        const webrtcError = err as WebRTCError
        setError(webrtcError)
        setCallState('ended')
        throw webrtcError
      }
    },
    [],
  )

  /**
   * End WebRTC call
   */
  const endCall = useCallback(async () => {
    try {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
      await webrtcService.closeConnection()
      setCallState('ended')
      setConnectionState(null)
      setIsMuted(false)
      // Reset the speaking flag: a stale `true` would break the anti-echo
      // auto-mute on the next call (the mute effect never sees false→true).
      setIsAgentSpeaking(false)
    } catch (err) {
      const webrtcError = err as WebRTCError
      setError(webrtcError)
      throw webrtcError
    }
  }, [])

  /**
   * Toggle microphone mute
   */
  const toggleMute = useCallback(() => {
    const newMutedState = !isMuted
    webrtcService.setMuted(newMutedState)
    setIsMuted(newMutedState)
  }, [isMuted])

  /**
   * Cleanup on unmount
   */
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      webrtcService.closeConnection()
    }
  }, [])

  return {
    callState,
    connectionState,
    isMuted,
    isAgentSpeaking,
    error,
    requestPermission,
    startCall,
    endCall,
    toggleMute,
  }
}
