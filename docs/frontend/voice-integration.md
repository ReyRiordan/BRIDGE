# Voice integration

How the voice client (`voice-client.md`) and the phase machine (`app-shell.md`)
become one running game. All of it lives in `web/src/voice/useVoiceSession.ts`
— the reducer stays pure, and `App` only composes the hook's status with
`state.phase` to decide what renders.

**One game = one session.** There is no in-place reset and no mid-game resume.

## The two ids

| Id | Minted by | Lifetime | Job |
|---|---|---|---|
| `session_id` | the browser, `crypto.randomUUID()` | one whole game | The domain key in every control-plane path. The server treats it as a free-form string. |
| `runtime_session_id` | `POST /voice/{session_id}/start` | one connect attempt | AgentCore container affinity. Every attempt — first connect or retry — takes a fresh one; a stale key pins signaling to a container that may be gone. |

`/start` creates no state, so re-calling it under the same `session_id` is the
sanctioned way to get a new affinity key.

## Lifecycle

```
IntroScreen "Begin"
  → requestPermission()            inside the click gesture, or the prompt never shows
  → session_id = randomUUID()
  → POST /voice/{id}/start         → runtime_session_id + ice_servers
  → startCall(..., RELAY_ONLY)     RELAY_ONLY from src/config.ts
  → ICE connected                  → dispatch BEGIN → GameScreen

game events on the data channel → createGameEventHandler(dispatch) → reducer

game_over
  → settle (below)                 → endCall() → POST /end {runtime_session_id}
  → dispatch SHOW_END              → EndScreen over the frozen scene

mid-game drop → connectionLost     → EndScreen's connection_lost variant
Play Again    → voice.reset() + PLAY_AGAIN → Start → a brand-new session_id
```

`BEGIN` is dispatched **only** once ICE reaches `connected`. A failed connect
therefore leaves the student on the intro screen with an inline error and a
Retry button, never on a dead game screen. While a connect is in flight the
Begin button is a disabled "Connecting…" with a status line — an AgentCore cold
start takes seconds.

## Reconnect policy

Bounded on purpose, and asymmetric around `BEGIN`:

| When | What happens |
|---|---|
| Pre-`BEGIN`, `ICE_STALL` (the hook's 10 s timeout) | **One** silent retry: re-`POST /start` for a fresh `runtime_session_id`, `startCall` again, still showing "Connecting…". |
| Pre-`BEGIN`, second `ICE_STALL` | Inline intro error, manual Retry. |
| Pre-`BEGIN`, any other failure (`CONNECTION_FAILED`, a 502 from `/start`) | No retry — it is server-side, and a second attempt fails the same way. |
| Post-`BEGIN`, any drop | **Zero** automatic retries → `connectionLost`. |

Game state lives in memory on the container. A mid-game reconnect that lands on
a *cold* container restarts the game at its reset escalation, and one that lands
on the *warm* one resumes a possibly-terminal session (`../backend/voice-kit/05-gotchas.md`
#30) — so a silent resume can only lie to the student. The loss is shown
instead. The kit's documented `initial_history` reconnect loop is deliberately
unused here: it restores LLM context, not game state, and never reaches the
browser.

## End settle

`game_over` arrives **before** the closing `transcript_update{patient}` and
before its TTS audio starts, so waiting naively on `isAgentSpeaking === false`
would fire instantly and cut the patient off. The hook waits for a rising *then*
falling edge:

| Step | Window | Why |
|---|---|---|
| Wait for audio to START | 3 s | If nothing starts (a timeout fail has no closing line), this window *is* the settle. |
| Wait for audio to FINISH | — | The service already debounces silence by 500 ms. |
| Hard cap | 20 s | Backstop, and safely inside the runtime's 45 s `GAME_GRACE_SECONDS` (`runtime/bridge/config.py`) — after that the reaper kills the pipeline and the audio dies mid-line anyway. |

On settle: `endCall()` → `POST /end` with `{runtime_session_id}` → `SHOW_END`.
`/end` is guarded by a once-flag, so the settle, a drop and unmount can all race
for it and it still fires exactly once. A drop **after** `game_over` counts as
settled — the audio is dead either way — and gives the normal debrief, not the
connection-lost one.

## Connection lost is not a game `fail`

The wire `game_over.status` stays `success | fail`. The lost-connection state is
a third **UI-level** variant of `EndScreen` (`EndOutcome`) with its own title
and copy, and the same Play Again button. A network drop is not a failed
de-escalation and must never be scored as one.

It also outranks `game_over` in `App.tsx`, which is why the runtime must keep
the pipeline alive for the whole game: its idle backstop sits outside
`time_limit + grace` (`../backend/voice-kit/05-gotchas.md` #35), so a run to the
time limit still receives `game_over` and gets the timeout debrief rather than
this variant.

## Mic status

The anti-echo auto-mute in `useWebRTC` silences the student's track while the
patient speaks (otherwise speaker output re-triggers the server-side VAD). That
is invisible on its own, so `components/MicStatus.tsx` renders a read-only pill
over the scene — "🎤 Listening" ↔ "🔇 Patient speaking". Deliberately not a
toggle: a manual control just invites fighting the auto-mute.

## Abandonment

Tab close or navigate-away runs the hook's unmount cleanup — `endCall()` plus a
best-effort `/end`. No `pagehide` beacon: `/end` is best-effort by design and
the runtime's 180 s idle timeout plus its session sweep are the real backstop.
