/**
 * WebRTC + voice-API type definitions.
 *
 * The API types mirror the backend control-plane schemas
 * (backend/voice_kit/control_plane/schemas.py) — keep them in sync (or
 * generate them from your OpenAPI schema and re-export here).
 */

/** Call state for a voice session. */
export type CallState = 'idle' | 'connecting' | 'connected' | 'ended'

/** WebRTC error surface: MICROPHONE_PERMISSION_DENIED | CONNECTION_FAILED | ICE_STALL. */
export interface WebRTCError {
  code: string
  message: string
}

/** A single ICE server (KVS managed-TURN) for the browser's RTCPeerConnection. */
export interface IceServerConfig {
  urls: string[]
  username?: string | null
  credential?: string | null
}

/**
 * Start-session response.
 *
 * `runtime_session_id` is the AgentCore affinity key the browser pins all
 * signaling to (via the `/signal` proxy). On a cold-start ICE failure the
 * frontend re-starts to mint a fresh `runtime_session_id`, same `session_id`.
 *
 * `ice_servers` are the browser's OWN KVS managed-TURN servers — the browser
 * must build its RTCPeerConnection with these (relay-only).
 */
export interface StartSessionResponse {
  runtime_session_id: string
  session_id: string
  ice_servers: IceServerConfig[]
}

/**
 * WebRTC signaling request proxied to the voice runtime.
 * Carries the affinity key plus the browser's non-trickle SDP offer.
 */
export interface SignalRequest {
  runtime_session_id: string
  sdp: string
  type: string
}

/** The voice runtime's SDP answer (relay-only candidates baked in). */
export interface SignalResponse {
  sdp: string
  type: string
}

/**
 * End-session response: whatever transcript the backend's on_end hook returned.
 * BRIDGE registers no `on_end` hook, so this is always `[]` — the live game
 * events are the transcript of record.
 */
export interface EndSessionResponse {
  message: string
  transcript: unknown[]
}

/** Optional body for the end endpoint — lets the runtime tear down immediately. */
export interface EndSessionRequest {
  runtime_session_id: string
}

/** WebRTC service interface. */
export interface WebRTCService {
  /**
   * Fired with each JSON payload parsed off the data channel. Deliberately
   * `unknown`: the reducer is the single validation point (see
   * ./gameEvents.ts).
   */
  onGameEvent: ((event: unknown) => void) | null
  requestMicrophonePermission(): Promise<MediaStream>
  /**
   * @param relayOnly - pin `iceTransportPolicy: 'relay'`. Defaults to TRUE and
   *   must stay true for every deployed path (the runtime is in a VPC with no
   *   browser-reachable host candidates). Pass false ONLY for local dev, where
   *   both peers are on loopback and there is no TURN at all.
   */
  initializeConnection(
    sessionId: string,
    runtimeSessionId: string,
    iceServers?: IceServerConfig[],
    relayOnly?: boolean,
  ): Promise<void>
  closeConnection(): Promise<void>
  setMuted(muted: boolean): void
  getConnectionState(): RTCPeerConnectionState | null
  getIceConnectionState(): RTCIceConnectionState | null
}
