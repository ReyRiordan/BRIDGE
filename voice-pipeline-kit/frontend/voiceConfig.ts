/**
 * Voice client configuration values.
 */

/**
 * WebRTC configuration.
 *
 * AUDIO_CONSTRAINTS: echoCancellation/noiseSuppression/autoGainControl all on —
 * the anti-echo auto-mute in useWebRTC is the second line of defense, not the
 * first. SAMPLE_RATE must match the runtime's TransportParams
 * audio_in_sample_rate (16 kHz, the STT path's rate).
 */
export const WEBRTC_CONFIG = {
  AUDIO_CONSTRAINTS: {
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  },
  SAMPLE_RATE: 16000, // Hz
} as const

/**
 * Unified session cap (minutes). Hardcoded — the user-facing timer that
 * auto-ends the call. MUST match the backend's SESSION_TIME_LIMIT_MINUTES
 * (voice_kit settings), which drives the runtime's independent
 * self-termination backstop.
 */
export const SESSION_TIME_LIMIT_MINUTES = 30
