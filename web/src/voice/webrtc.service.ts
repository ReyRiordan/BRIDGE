/**
 * WebRTC Service
 *
 * Manages the WebRTC connection for voice sessions against the Pipecat voice
 * runtime on AWS Bedrock AgentCore. Signaling is proxied through the API
 * control plane (the runtime data-plane emits no CORS): the browser builds a
 * non-trickle SDP offer and POSTs it to `{basePath}/{id}/signal`, then sets the
 * returned answer. The browser builds its RTCPeerConnection with its OWN KVS
 * managed-TURN ICE servers (from the start endpoint) and pins
 * `iceTransportPolicy: 'relay'` — relay-only WebRTC needs each peer to hold its
 * own TURN allocation; without them the browser offers only private host
 * candidates and the runtime's relay is rejected, stalling ICE. The live
 * transcript arrives as JSON `TranscriptItem`s over the WebRTC data channel.
 *
 * The one exception is local dev (`VITE_BRIDGE_LOCAL=1`), which passes
 * `relayOnly: false`: browser and runtime are both on loopback, so there is no
 * TURN to relay through. That divergence is also the mode's biggest trap — a
 * feature that works on host candidates can still stall behind TURN in the
 * cloud, so the relay-only post-deploy check stays mandatory.
 */

import { signalVoiceSession } from './voiceApi'
import { WEBRTC_CONFIG } from './voiceConfig'
import type {
  IceServerConfig,
  TranscriptItem,
  WebRTCError,
  WebRTCService,
} from './webrtc.types'

// Minimum average frequency-bin value (0–255 scale) that counts as agent speech.
const REMOTE_AUDIO_THRESHOLD = 10
// How long the remote stream must stay silent before isAgentSpeaking → false.
const REMOTE_SILENCE_DEBOUNCE_MS = 500

/**
 * WebRTC Service Class
 * Handles RTCPeerConnection lifecycle, microphone access, signaling via the
 * `/signal` proxy, and transcript delivery over the data channel.
 */
class WebRTCServiceClass implements WebRTCService {
  private peerConnection: RTCPeerConnection | null = null
  private localStream: MediaStream | null = null
  private audioContext: AudioContext | null = null
  private animationFrameId: number | null = null
  private remoteAudio: HTMLAudioElement | null = null
  private silenceTimer: ReturnType<typeof setTimeout> | null = null

  /** Fired when remote (agent) audio starts/stops playing. */
  onRemoteAudioStateChange: ((playing: boolean) => void) | null = null
  /** Fired with each finalized transcript turn received over the data channel. */
  onTranscriptMessage: ((item: TranscriptItem) => void) | null = null

  /**
   * Request microphone permission from user
   * @returns MediaStream with audio track
   * @throws WebRTCError if permission denied or not available
   */
  async requestMicrophonePermission(): Promise<MediaStream> {
    try {
      const stream = await navigator.mediaDevices.getUserMedia(
        WEBRTC_CONFIG.AUDIO_CONSTRAINTS,
      )
      this.localStream = stream
      return stream
    } catch (error) {
      const webrtcError: WebRTCError = {
        code: 'MICROPHONE_PERMISSION_DENIED',
        message:
          error instanceof Error
            ? error.message
            : 'Failed to access microphone. Please grant permission.',
      }
      throw webrtcError
    }
  }

  /**
   * Initialize the WebRTC connection with the voice runtime.
   *
   * @param sessionId - host session id (path param for `/signal`)
   * @param runtimeSessionId - AgentCore affinity key all signaling pins to
   * @throws WebRTCError if connection fails
   *
   * Flow (non-trickle, single round-trip):
   * 1. Ensure microphone access.
   * 2. Create RTCPeerConnection with the browser's own KVS TURN servers, relay-only
   *    (or `'all'` when relayOnly is false — local dev).
   * 3. Add local audio track + create the `data` channel (transcript + runtime handshake).
   * 4. Create the SDP offer, set local description, wait for ICE gathering to complete.
   * 5. POST the offer to `{basePath}/{id}/signal { runtime_session_id, sdp, type }`.
   * 6. Set the runtime's SDP answer as the remote description.
   *
   * @param iceServers - the browser's own KVS managed-TURN servers. REQUIRED for
   *   the relay path to establish; if empty, ICE will stall (each WebRTC peer
   *   needs its own TURN allocation — the runtime's candidates are not reusable).
   * @param relayOnly - defaults to true. False only for local dev (loopback
   *   host candidates, no TURN); never for a deployed backend.
   */
  async initializeConnection(
    sessionId: string,
    runtimeSessionId: string,
    iceServers?: IceServerConfig[],
    relayOnly: boolean = true,
  ): Promise<void> {
    try {
      // Ensure we have microphone access
      if (!this.localStream) {
        await this.requestMicrophonePermission()
      }

      // Build with the browser's OWN KVS TURN servers, pinned relay-only — the
      // runtime is in a VPC with no browser-reachable host candidates, so media
      // must flow over the KVS-managed TURN relay on both sides.
      const rtcIceServers: RTCIceServer[] = (iceServers ?? []).map((s) => ({
        urls: s.urls,
        ...(s.username ? { username: s.username } : {}),
        ...(s.credential ? { credential: s.credential } : {}),
      }))
      // Only a warning under relay-only: with no TURN servers there is
      // nothing to relay through. In local mode empty is the expected state.
      if (relayOnly && rtcIceServers.length === 0) {
        console.warn(
          'No ICE servers provided to voice connection; ICE will likely stall.',
        )
      }
      // A mid-call reconnect reaches here without an intervening closeConnection;
      // close the previous peer connection so it isn't orphaned.
      if (this.peerConnection) {
        this.peerConnection.close()
        this.peerConnection = null
      }
      this.peerConnection = new RTCPeerConnection({
        iceServers: rtcIceServers,
        iceTransportPolicy: relayOnly ? 'relay' : 'all',
      })

      // Add local audio track
      if (this.localStream) {
        this.localStream.getTracks().forEach((track) => {
          this.peerConnection?.addTrack(track, this.localStream!)
        })
      }

      // Data channel carries the live transcript (and triggers the runtime's
      // pipeline, which blocks on the channel being established).
      const dataChannel = this.peerConnection.createDataChannel('data')
      dataChannel.onmessage = (event) => this.handleDataChannelMessage(event)

      // Handle incoming audio tracks from the agent
      this.peerConnection.ontrack = (event) => {
        // Stop any analysis loop / debounce timer from a previous track so the
        // instance fields below track exactly one active analysis.
        this.stopAudioAnalysis()

        // Reuse a single audio element across tracks/reconnects; a fresh local
        // element per ontrack would leak the element and its MediaStream.
        if (!this.remoteAudio) {
          this.remoteAudio = new Audio()
        }
        this.remoteAudio.srcObject = event.streams[0]
        this.remoteAudio.play().catch((error) => {
          console.error('Failed to play audio:', error)
        })

        // Detect agent speech via audio level analysis. The audio element's
        // onplay fires at connection time (not when TTS content arrives), so
        // we use an AnalyserNode to detect actual audio activity. ontrack can
        // fire more than once — reuse the existing AudioContext if present.
        if (!this.audioContext) {
          this.audioContext = new AudioContext()
        }
        const source = this.audioContext.createMediaStreamSource(
          event.streams[0],
        )
        const analyser = this.audioContext.createAnalyser()
        analyser.fftSize = 256
        source.connect(analyser)

        const dataArray = new Uint8Array(analyser.frequencyBinCount)
        let isSpeaking = false

        const checkLevel = () => {
          analyser.getByteFrequencyData(dataArray)
          const avg =
            dataArray.reduce((sum, val) => sum + val, 0) / dataArray.length

          if (avg > REMOTE_AUDIO_THRESHOLD) {
            if (this.silenceTimer) {
              clearTimeout(this.silenceTimer)
              this.silenceTimer = null
            }
            if (!isSpeaking) {
              isSpeaking = true
              this.onRemoteAudioStateChange?.(true)
            }
          } else if (isSpeaking && !this.silenceTimer) {
            this.silenceTimer = setTimeout(() => {
              isSpeaking = false
              this.silenceTimer = null
              this.onRemoteAudioStateChange?.(false)
            }, REMOTE_SILENCE_DEBOUNCE_MS)
          }

          this.animationFrameId = requestAnimationFrame(checkLevel)
        }

        this.animationFrameId = requestAnimationFrame(checkLevel)
      }

      // Create and send SDP offer
      const offer = await this.peerConnection.createOffer()
      await this.peerConnection.setLocalDescription(offer)

      // Non-trickle ICE: the runtime needs a complete SDP with all candidates
      // before it can route media back. Wait for gathering to finish so
      // localDescription contains the full candidate list.
      await new Promise<void>((resolve) => {
        if (this.peerConnection!.iceGatheringState === 'complete') {
          resolve()
        } else {
          const onStateChange = () => {
            if (this.peerConnection!.iceGatheringState === 'complete') {
              this.peerConnection!.removeEventListener(
                'icegatheringstatechange',
                onStateChange,
              )
              resolve()
            }
          }
          this.peerConnection!.addEventListener(
            'icegatheringstatechange',
            onStateChange,
          )
        }
      })

      // Proxy the offer to the voice runtime and apply the answer.
      const answer = await signalVoiceSession(sessionId, {
        runtime_session_id: runtimeSessionId,
        sdp: this.peerConnection.localDescription!.sdp,
        type: this.peerConnection.localDescription!.type,
      })

      await this.peerConnection.setRemoteDescription(
        new RTCSessionDescription({
          sdp: answer.sdp,
          type: answer.type as RTCSdpType,
        }),
      )
    } catch (error) {
      // Clean up on error
      await this.closeConnection()

      const webrtcError: WebRTCError = {
        code: 'CONNECTION_FAILED',
        message:
          error instanceof Error
            ? error.message
            : 'Failed to establish WebRTC connection',
      }
      throw webrtcError
    }
  }

  /**
   * Parse a transcript message off the data channel and forward it to the
   * registered callback. Malformed payloads are ignored.
   */
  private handleDataChannelMessage(event: MessageEvent): void {
    if (typeof event.data !== 'string') return
    try {
      const item = JSON.parse(event.data) as TranscriptItem
      if (
        item &&
        (item.role === 'user' || item.role === 'assistant') &&
        typeof item.content === 'string'
      ) {
        this.onTranscriptMessage?.(item)
      }
    } catch {
      // Non-JSON / control frame — ignore.
    }
  }

  /**
   * Cancel the remote audio-level analysis loop and its silence-debounce timer.
   * The timer would otherwise fire up to REMOTE_SILENCE_DEBOUNCE_MS after
   * teardown, invoking onRemoteAudioStateChange on a re-mounted hook.
   */
  private stopAudioAnalysis(): void {
    if (this.animationFrameId !== null) {
      cancelAnimationFrame(this.animationFrameId)
      this.animationFrameId = null
    }
    if (this.silenceTimer) {
      clearTimeout(this.silenceTimer)
      this.silenceTimer = null
    }
  }

  /**
   * Close WebRTC connection and release resources
   */
  async closeConnection(): Promise<void> {
    // Stop audio level polling
    this.stopAudioAnalysis()

    // Release the remote audio element and its MediaStream
    if (this.remoteAudio) {
      this.remoteAudio.pause()
      this.remoteAudio.srcObject = null
      this.remoteAudio = null
    }

    if (this.audioContext) {
      try {
        await this.audioContext.close()
      } catch {
        // Already closed — nothing to release.
      }
      this.audioContext = null
    }

    // Stop local tracks
    if (this.localStream) {
      this.localStream.getTracks().forEach((track) => track.stop())
      this.localStream = null
    }

    // Close peer connection
    if (this.peerConnection) {
      this.peerConnection.close()
      this.peerConnection = null
    }
  }

  /**
   * Set microphone muted state
   * @param muted - true to mute, false to unmute
   */
  setMuted(muted: boolean): void {
    if (this.localStream) {
      this.localStream.getAudioTracks().forEach((track) => {
        track.enabled = !muted
      })
    }
  }

  /**
   * Get current connection state
   * @returns RTCPeerConnectionState or null if not connected
   */
  getConnectionState(): RTCPeerConnectionState | null {
    return this.peerConnection?.connectionState ?? null
  }

  /**
   * Get current ICE connection state
   * @returns RTCIceConnectionState or null if not connected
   */
  getIceConnectionState(): RTCIceConnectionState | null {
    return this.peerConnection?.iceConnectionState ?? null
  }
}

// Export singleton instance
export const webrtcService = new WebRTCServiceClass()
