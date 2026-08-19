# Voice client

The browser half of the voice pipeline: five vendored files in `web/src/voice/`, no page component. They are kept byte-close to their upstream (pipeline-kit) source — ESLint carries a scoped override for the folder instead of editing them. [Rewrite G] wires them into the simulation screen.

| File | What it is |
|---|---|
| `webrtc.types.ts` | All types (call state, errors, transcript, API request/response shapes) |
| `voiceConfig.ts` | `WEBRTC_CONFIG` (mic constraints, 16 kHz) + `SESSION_TIME_LIMIT_MINUTES` |
| `voiceApi.ts` | The three control-plane calls, transport-injected |
| `webrtc.service.ts` | Singleton service: peer connection, relay-only ICE, non-trickle offer, data-channel transcript, remote-audio analysis |
| `useWebRTC.ts` | React hook: call lifecycle, ICE-stall detection, anti-echo auto-mute |
| `gameEvents.gen.ts` | **Generated** — the v1 data-channel event envelope (see below) |

## 1. Configure the API transport (once, at startup)

```ts
import axios from 'axios'
import { configureVoiceApi } from './voice/voiceApi'

const api = axios.create({ baseURL: '/api/v1' /* + your auth interceptors */ })
configureVoiceApi(
  { post: async (path, body) => (await api.post(path, body)).data },
  '/voice' // must match the router prefix passed to create_voice_router()
)
```

## 2. Connect flow

```tsx
import { useWebRTC } from './voice/useWebRTC'
import { startVoiceSession, endVoiceSession } from './voice/voiceApi'

const { callState, transcript, isAgentSpeaking, isMuted, toggleMute,
        requestPermission, startCall, endCall, connectionState, error } = useWebRTC()

// On mount: request mic permission early so failures surface before the call.
await requestPermission()

// Connect. EVERY attempt (first connect or reconnect) requests a fresh
// runtime_session_id from /start — the host session_id stays the same, so the
// backend context provider reloads the same conversation.
const connectWithFreshRuntime = async () => {
  const { runtime_session_id, ice_servers } = await startVoiceSession(sessionId)
  await startCall(sessionId, runtime_session_id, ice_servers)
}
await connectWithFreshRuntime()

// Render `transcript` (kit roles: 'user' | 'assistant') — it streams in live
// over the WebRTC data channel. BRIDGE's own game events ride the same channel
// under the v1 envelope (roles there are 'student' | 'patient').
```

## 3. Reconnect loop (recommended)

A cold-started runtime can stall ICE (`startCall` throws `ICE_STALL`), and a
live call can drop (`connectionState` becomes `'failed'`/`'disconnected'`).
Both recover the same way: exponential backoff, max 3 attempts, **fresh
`runtime_session_id` each attempt**:

```tsx
useEffect(() => {
  if (callState !== 'connected') return
  if (connectionState !== 'failed' && connectionState !== 'disconnected') return

  if (reconnectAttempts < 3) {
    const delay = Math.pow(2, reconnectAttempts) * 1000 // 1s, 2s, 4s
    const t = setTimeout(async () => {
      setReconnectAttempts((n) => n + 1)
      try {
        await connectWithFreshRuntime()
      } catch {
        setReconnectTick((t) => t + 1) // re-fire this effect if the attempt itself throws
      }
    }, delay)
    return () => clearTimeout(t)
  }
  surfaceError('Connection lost after 3 reconnect attempts.')
}, [connectionState, callState, reconnectTick])
```

## 4. End flow

```ts
await endCall()                                  // tear down WebRTC locally
const { transcript } = await endVoiceSession(sessionId) // backend on_end hook: authoritative transcript
```

The server-side transcript from `/end` is authoritative — the live data-channel
copy can miss turns delivered during a reconnect gap.

`/end` accepts an optional body `{ runtime_session_id }` — only the browser
holds the current affinity key, and sending it lets the control plane tear the
runtime's pipeline down immediately (best-effort; the idle timeout is the
backstop). The client sends it in [Rewrite G].

## Behavior notes

- **Anti-echo auto-mute**: the hook mutes the mic while `isAgentSpeaking` (detected via an `AnalyserNode` on the remote stream, debounced 500 ms — not `audio.onplay`, which fires at connection time). Without this, speaker output re-triggers the server-side VAD.
- **Timer**: the runtime is authoritative — drive the countdown from the `timer` game event. `SESSION_TIME_LIMIT_MINUTES` (`voiceConfig.ts`) must stay aligned with the backend's `SESSION_TIME_LIMIT_MINUTES` — a separate knob from the runtime's `IDLE_TIMEOUT_SECS` self-termination backstop.
- The service is a singleton — one active call per tab.

## Game events (generated types)

Everything BRIDGE pushes over the data channel is a `GameEvent` from `gameEvents.gen.ts`: `transcript_update`, `state_update`, `action_detected`, `timer`, `game_over`, each stamped `v: 1` and discriminated on `type`.

The file is generated from `runtime/bridge/events.py` and committed — never edit it by hand:

```bash
python3 scripts/gen_event_types.py           # regenerate
python3 scripts/gen_event_types.py --check   # what Backend CI runs
```
