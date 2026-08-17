/**
 * Voice control-plane API calls.
 *
 * Transport-injected so the kit doesn't assume your HTTP client: call
 * `configureVoiceApi(...)` once at startup with a thin adapter over your
 * axios/fetch instance (which is where your auth headers live), then the three
 * endpoint functions target the routes served by
 * backend/voice_kit/control_plane/router.py (default prefix '/voice').
 */

import type {
  EndSessionResponse,
  SignalRequest,
  SignalResponse,
  StartSessionResponse,
} from './webrtc.types'

/** Minimal HTTP transport the host supplies (auth headers included). */
export interface VoiceApiTransport {
  post<T>(path: string, body?: unknown): Promise<T>
}

let transport: VoiceApiTransport | null = null
let basePath = '/voice'

/**
 * Register the host HTTP client. Example (axios):
 *
 *   configureVoiceApi({
 *     post: async (path, body) => (await api.post(path, body)).data,
 *   })
 */
export const configureVoiceApi = (
  t: VoiceApiTransport,
  base: string = '/voice',
): void => {
  transport = t
  basePath = base
}

const getTransport = (): VoiceApiTransport => {
  if (!transport) {
    throw new Error('voiceApi not configured — call configureVoiceApi() first')
  }
  return transport
}

/**
 * Start (or resume) a voice session — runs the backend's on_start hook and
 * mints a fresh AgentCore `runtime_session_id` the browser pins all signaling
 * to, plus the browser's own KVS managed-TURN ice_servers.
 * POST {basePath}/{id}/start
 */
export const startVoiceSession = (id: string): Promise<StartSessionResponse> =>
  getTransport().post(`${basePath}/${id}/start`)

/**
 * Signal a WebRTC offer to the voice runtime via the control-plane proxy and
 * return its SDP answer (relay-only candidates baked in). Non-trickle, single
 * round-trip.
 * POST {basePath}/{id}/signal
 */
export const signalVoiceSession = (
  id: string,
  body: SignalRequest,
): Promise<SignalResponse> =>
  getTransport().post(`${basePath}/${id}/signal`, body)

/**
 * End the voice session — runs the backend's on_end hook and returns its
 * transcript.
 * POST {basePath}/{id}/end
 */
export const endVoiceSession = (id: string): Promise<EndSessionResponse> =>
  getTransport().post(`${basePath}/${id}/end`)
