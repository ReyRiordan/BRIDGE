# Frontend wiring

Five files, no page component — wire them into your own UI. Copy them anywhere in your `src/` (adjust relative imports if you split them across folders).

| File | What it is |
|---|---|
| `webrtc.types.ts` | All types (call state, errors, transcript, API request/response shapes) |
| `voiceConfig.ts` | `WEBRTC_CONFIG` (mic constraints, 16 kHz) + `SESSION_TIME_LIMIT_MINUTES` |
| `voiceApi.ts` | The three control-plane calls, transport-injected |
| `webrtc.service.ts` | Singleton service: peer connection, relay-only ICE, non-trickle offer, data-channel transcript, remote-audio analysis |
| `useWebRTC.ts` | React hook: call lifecycle, ICE-stall detection, anti-echo auto-mute |

## 1. Configure the API transport (once, at startup)

```ts
import axios from 'axios'
import { configureVoiceApi } from './voiceApi'

const api = axios.create({ baseURL: '/api/v1' /* + your auth interceptors */ })
configureVoiceApi(
  { post: async (path, body) => (await api.post(path, body)).data },
  '/voice' // must match the router prefix passed to create_voice_router()
)
```

## 2. Connect flow

```tsx
import { useWebRTC } from './useWebRTC'
import { startVoiceSession, endVoiceSession } from './voiceApi'

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

// Render `transcript` (roles: 'user' | 'assistant') — it streams in live over
// the WebRTC data channel.
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

## Behavior notes

- **Anti-echo auto-mute**: the hook mutes the mic while `isAgentSpeaking` (detected via an `AnalyserNode` on the remote stream, debounced 500 ms — not `audio.onplay`, which fires at connection time). Without this, speaker output re-triggers the server-side VAD.
- **Timer**: drive your own countdown from `SESSION_TIME_LIMIT_MINUTES` (`voiceConfig.ts`); the runtime independently self-terminates at the same limit.
- The service is a singleton — one active call per tab.
