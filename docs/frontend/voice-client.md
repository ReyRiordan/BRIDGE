# Voice client

The browser half of the voice pipeline: the vendored files in `web/src/voice/`, no page component. They are kept close to their upstream (pipeline-kit) source — ESLint carries a scoped override for the folder. [Rewrite G2] mounts them on the simulation screen.

| File | What it is |
|---|---|
| `webrtc.types.ts` | All types (call state, errors, API request/response shapes) |
| `voiceConfig.ts` | `WEBRTC_CONFIG` (mic constraints, 16 kHz) + `SESSION_TIME_LIMIT_MINUTES` |
| `voiceApi.ts` | The three control-plane calls, transport-injected |
| `webrtc.service.ts` | Singleton service: peer connection, relay-only ICE, non-trickle offer, data-channel game events, remote-audio analysis |
| `useWebRTC.ts` | React hook: call lifecycle, ICE-stall detection, anti-echo auto-mute |
| `gameEvents.ts` | `createGameEventHandler(dispatch)` — the data channel → reducer seam |
| `gameEvents.gen.ts` | **Generated** — the v1 data-channel event envelope (see below) |

## 1. Startup bootstrap (once, in `main.tsx`)

Nothing renders until the control-plane base URL is known, because it is only
known at runtime in a deployed build:

```ts
const baseUrl = await resolveApiBaseUrl()          // src/config.ts
configureVoiceApi(createTransport(baseUrl), `${baseUrl}/voice`)
```

- `resolveApiBaseUrl()` returns `''` under `BRIDGE_LOCAL` (same-origin, the Vite
  proxy owns `/voice` and `/scenario`); otherwise it fetches
  `/amplify_outputs.json` and reads `custom.apiUrl`, trailing slash trimmed.
  It memoizes; `getApiBaseUrl()` is the sync read used by `src/api/scenario.ts`.
  A build-time `VITE_API_URL` was rejected on purpose — it would couple the SPA
  build to backend deploy ordering.
- The transport is a small `fetch` adapter (no axios dependency): JSON in/out,
  non-2xx throws carrying the server's `{detail}` (the control plane returns
  502-with-CORS for upstream failures). An absent body stays absent — `/end`'s
  request model is optional and `null` would fail validation.
- `/voice` must match the router prefix passed to `create_voice_router()`.
- Bootstrap failure renders a load-error message instead of the app.

## 2. Connect flow

```tsx
import { useWebRTC } from './voice/useWebRTC'
import { startVoiceSession, endVoiceSession } from './voice/voiceApi'

const { callState, isAgentSpeaking, isMuted, toggleMute,
        requestPermission, startCall, endCall, connectionState, error } =
  useWebRTC({ onGameEvent: createGameEventHandler(dispatch) })

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
```

`initializeConnection` also caps the non-trickle ICE-gathering wait at 10 s: a
gathering stall would otherwise hang *before* the hook's ICE-stall detection
starts. It rejects as `CONNECTION_FAILED` through the normal cleanup path.

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
await endCall()                                          // tear down WebRTC locally
await endVoiceSession(sessionId, { runtime_session_id }) // best-effort runtime teardown
```

Always send the body: only the browser holds the current affinity key, and
without it the runtime's pipeline lives on until the 180 s idle timeout. The
call is always 200. `EndSessionResponse.transcript` is always `[]` in BRIDGE —
no `on_end` hook is registered, and the live game events are the transcript of
record.

## Relay-only, and the one exception

`initializeConnection` / `startCall` take a trailing `relayOnly` argument that **defaults to `true`**: the browser pins `iceTransportPolicy: 'relay'`, because the runtime sits in a VPC with no browser-reachable host candidates and each peer needs its own TURN allocation (gotcha #9).

The single caller that passes `false` is local dev, where both peers are on loopback and there is no TURN at all. It is driven by `RELAY_ONLY` in `src/config.ts`, which is `false` only when `web/.env.local` sets `VITE_BRIDGE_LOCAL=1`:

```tsx
import { RELAY_ONLY } from '../config'
await startCall(sessionId, runtime_session_id, ice_servers, RELAY_ONLY)
```

Never infer this from `import.meta.env.DEV`: `vite dev` against the deployed backend is a normal workflow, and handing that session host candidates hides the TURN-only reality until after deploy. Locally the SPA also reaches the control plane **same-origin**, through the Vite `server.proxy` for `/voice` and `/scenario` — so no CORS is involved and the resolved API base stays empty. See [`../backend/local-dev.md`](../backend/local-dev.md).

## Behavior notes

- **Anti-echo auto-mute**: the hook mutes the mic while `isAgentSpeaking` (detected via an `AnalyserNode` on the remote stream, debounced 500 ms — not `audio.onplay`, which fires at connection time). Without this, speaker output re-triggers the server-side VAD.
- **Timer**: the runtime is authoritative — drive the countdown from the `timer` game event. `SESSION_TIME_LIMIT_MINUTES` (`voiceConfig.ts`) must stay aligned with the backend's `SESSION_TIME_LIMIT_MINUTES` — a separate knob from the runtime's `IDLE_TIMEOUT_SECS` self-termination backstop.
- The service is a singleton — one active call per tab.

## Game events (generated types)

Everything BRIDGE pushes over the data channel is a `GameEvent` from `gameEvents.gen.ts`: `transcript_update`, `state_update`, `action_detected`, `timer`, `game_over`, each stamped `v: 1` and discriminated on `type`.

**The channel carries the v1 envelope only** — `transcript_update` uses roles `student`/`patient`, never the kit's `user`/`assistant`.

The path from wire to UI is deliberately flat, with **one validation point**:

```
data channel → handleDataChannelMessage (JSON.parse only)
             → onGameEvent → createGameEventHandler → dispatch → gameReducer
```

`handleDataChannelMessage` drops only unparseable payloads (DEV-only
`console.warn`; a bad frame never ends the call) and forwards everything else
untouched as `unknown`. `createGameEventHandler` guards only "not a plain
object". Unknown `type` and `v !== 1` are dropped by the reducer's
`isAcceptable` and nowhere else — duplicating that check would let the two
definitions of "acceptable" drift apart.

Ordering the reducer relies on: connect → one authoritative `state_update`;
per turn `transcript_update{student}` → `action_detected`×N → `state_update` →
(`game_over`?) → `transcript_update{patient}`; `timer` at 1 Hz.

The file is generated from `runtime/bridge/events.py` and committed — never edit it by hand:

```bash
python3 scripts/gen_event_types.py           # regenerate
python3 scripts/gen_event_types.py --check   # what Backend CI runs
```
