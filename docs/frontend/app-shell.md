# App shell

`web/src/` — the four screens and the state machine behind them. The voice
session hangs off this same reducer: the data channel dispatches into it
(`voice-client.md`) and the session lifecycle around it lives in
`voice-integration.md`.

## Phase machine

One pure reducer (`src/state/gameState.ts`) owns the screen phase *and* all game
state, so there is no second source of truth to keep in sync.

| Phase | Screen | Leaves on |
|---|---|---|
| `start` | `StartScreen` — fetches `GET /scenario` | `SCENARIO_LOADED` |
| `intro` | `IntroScreen` — case text + goal | `BEGIN` |
| `game` | `GameScreen` — scene, escalation bar, transcript | `SHOW_END` |
| `end` | `GameScreen` + `EndScreen` overlay | `PLAY_AGAIN` → `start` |

`SCENARIO_LOADED` is the only action carrying a payload; `PLAY_AGAIN` resets to
`initialState` and drops the cached scenario, so a replay refetches `/scenario`
and picks up scenario edits.

## Reducer contract

Wire events come straight off `src/voice/gameEvents.gen.ts`. Control actions are
`SCREAMING_SNAKE` and wire discriminants are `lower_snake`, so the two can share
one `type` union — the voice client dispatches a parsed data-channel message
with no adapter.

| Event | Fields touched |
|---|---|
| `state_update` | `escalation`, `max`, `activeActions`, `status` |
| `action_detected` | adds to `actionsTaken`; sets `lastAction` with a fresh `seq` id |
| `transcript_update` | appends a `TranscriptEntry` |
| `timer` | `timer.elapsed`, `timer.limit` |
| `game_over` | `gameOver`, once — a second event returns the identical state. **Does not change phase.** |

Unknown `type` values and any envelope where `v !== 1` are dropped, returning the
*identical* state object (`console.warn` under `import.meta.env.DEV`). A newer
runtime can add events without breaking a deployed SPA.

## Rules

**Everything is scenario-derived.** No escalation max, starting value, time
limit or action list is hardcoded. Editing `resources/scenario_1.json` changes
the UI with zero code edits — `gameReducer.test.ts` asserts exactly that.

**No wall-clock in the reducer.** The reducer is a pure function of
`(state, action)`. Timed behaviour lives in whatever owns it: the 3 s badge
auto-hide in `ActionBadge`, and the end handoff in `useVoiceSession`, which
holds the final frame until the closing patient audio has actually finished
before dispatching `SHOW_END` (see `voice-integration.md`).

**The runtime owns the clock.** `TimerPill` is stateless and the UI never ends
the game itself — expiry arrives as a `game_over` event.

## Scene compositing

`selectLayers` (`src/state/selectors.ts`) builds the composite from the scenario:
every action with a non-null `layer`, plus a synthetic `__patient__` layer at
layer 0, sorted ascending into `z = index + 1`. The sort is stable and the
patient entry is appended last, so **same-layer ties resolve by the `actions`
array order**. For `scenario_1` that is Environmental (−1) → patient (0) →
Caregiver → Force IV → Restraint.

Each layer's src is `active` when the action is in `state_update.active_actions`
and `inactive` otherwise; `null` means the layer renders nothing (Force IV and
Restraint have no inactive art). `SceneLayer` keeps every src it has shown
mounted and cross-fades opacity rather than swapping one `<img>`'s src, and
`SceneStage` pins one fixed aspect (`aspect-stage`, the art's native 1196×880)
that all layers absolutely fill.

`state_update.status` is stored but deliberately not rendered.

## Styling

`src/index.css` is the whole design system — Tailwind v4 has no config file, so
the `@theme` block defines every token. Tone and variant classes must be picked
through lookup objects (`Record<EscTone, string>`), never template
interpolation: Tailwind scans source as plain text and `bg-esc-${tone}` emits no
CSS. The escalation scale is inverted — low escalation is the win state, so
`calm` is the green end.

## Testing

`src/state/goldenRun.test.ts` is the acceptance test: hand-authored typed
`GameEvent[]` runs are folded through the reducer and the projected
`toViewModel` is asserted at milestone frames, with invariants at every prefix.
The DOM tests render states produced by `foldTo(...)` over those same runs, and
assert on `data-layer` / `data-src` / zIndex wrappers — jsdom never loads an
image, so asserting on image state would assert on nothing.

## How the voice client plugs in

`dispatch` is the seam. Wire events are already in the action union and the
envelope tolerance lives here, so the voice client only calls
`createGameEventHandler(dispatch)` on whatever it parsed off the channel — no
adapter, no second filter. `App` mounts `useVoiceSession(state, dispatch)`,
which registers that handler and owns everything effectful about the session —
ids, connect, retry, end settle, drops. The end phase is reached only through
that hook: `game_over` sets `state.gameOver`, and `SHOW_END` follows once the
final audio has played (`voice-integration.md`). `EndScreen` also renders a
third, UI-only `connection_lost` variant that never comes from the wire.
