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
| `start` | `StartScreen` — the sign-in panel, then `GET /scenarios` as picker cards (disabled until signed in); a card fetches `GET /scenario/{id}` | `SCENARIO_LOADED` |
| `intro` | `IntroScreen` — the scenario's intro image, title, case text + goal | `BEGIN` |
| `game` | `GameScreen` — scene, escalation bar, transcript | `SHOW_END` |
| `end` | `GameScreen` + `EndScreen` overlay | `PLAY_AGAIN` → `start` |

`SCENARIO_LOADED` is the only action carrying a payload; `PLAY_AGAIN` resets to
`initialState` and drops the cached scenario, so a replay goes back to the picker,
refetches, and picks up scenario edits. The Start screen's loading and error
state are local; Retry re-runs whichever fetch failed (the list, or the chosen
card's config). Each card's thumbnail is `/visuals/<id>/<intro_image>`.

## Identity

The student signs in once, on the Start screen, and then runs as many cases as
they like. `IdentityPanel` (`src/components/`) sits above the picker with a
self-reported Rutgers NetID field, a Voice/Text segmented toggle, and the
recording notice, verbatim:

> Your NetID, your typed or transcribed responses, and your performance in each
> case are saved and reviewed by the course/research team. Audio is not recorded
> or stored.

There is no checkbox and no SSO. `normalizeNetId` (`src/identity.ts`) trims,
lowercases and strips an `@…` suffix, then requires `^[a-z]+[0-9]*$` at 2–20
characters. The API re-checks with the same rule. An invalid NetID shows an
inline error and blocks Continue.

Once confirmed, the panel collapses to "Signed in as **abc123** · Voice · Not
you? Change". `App` owns the `identity` state, which starts from `loadIdentity()`,
and stores it on every change in `localStorage` under `bridge.identity`
(`{netid, mode}`). Change clears it, for shared lab machines. Every storage call
is wrapped in try/catch, so blocked storage only means signing in again.
While `identity === null` every picker card is `disabled`.

The identity goes no further than `useVoiceSession`, which sends it to
`POST /sessions` on Begin (`voice-integration.md`). The mode decides how the
student talks: Voice uses the mic, and Text never asks for it (see
[Text mode](#text-mode)).

## Text mode

`App` passes `voice.mode` to `GameScreen`. In Text mode the screen swaps the
mic-specific UI for two controls:

- **`TextComposer`** sits under the transcript (the transcript keeps filling
  the column). Enter sends, Shift+Enter adds a newline, input is capped at
  1000 characters (the runtime's limit) with a counter in the last 100, and
  empty input is blocked. A failed send (`onSend` returns `false`, channel not
  open) keeps the draft and says "Not sent". The transcript's empty hint reads
  "Type below…".
- **`PatientMuteToggle`** replaces `MicStatus` in the scene's top-left corner
  (`SceneStage`'s `corner` slot). The patient still speaks every reply; mute
  only silences playback, and the replies are in the transcript either way.

**One message in flight.** `GameScreen` owns `awaitingReply`. A successful send
records how many patient turns the transcript holds, and the composer stays
disabled until that count changes, `gameOver` is set, or
`AWAITING_REPLY_FALLBACK_MS` (15 s) passes. The fallback covers a referee
timeout or a turn with no reply. Deriving it from the transcript means no
event can be missed. Focus returns to the box when it re-enables. Typed turns
use the same time limit and rules as spoken ones.

## Reducer contract

Wire events come straight off `src/voice/gameEvents.gen.ts`. Control actions are
`SCREAMING_SNAKE` and wire discriminants are `lower_snake`, so the two can share
one `type` union — the voice client dispatches a parsed data-channel message
with no adapter.

| Event | Fields touched |
|---|---|
| `state_update` | `escalation`, `max`, `activeActions`, `status` |
| `action_detected` | adds to `actionsTaken`; sets `lastAction` (incl. `repeat`, `false` when absent) with a fresh `seq` id. `ActionBadge` toasts it green (helpful), red (escalating), or — on a `repeat` — neutral grey as "`<action>` — already credited" |
| `transcript_update` | appends a `TranscriptEntry` |
| `timer` | `timer.elapsed`, `timer.limit` |
| `game_over` | `gameOver`, once — a second event returns the identical state. **Does not change phase.** |

Unknown `type` values and any envelope where `v !== 1` are dropped, returning the
*identical* state object (`console.warn` under `import.meta.env.DEV`). A newer
runtime can add events without breaking a deployed SPA.

## Rules

**Everything is scenario-derived.** No escalation max, starting value, time
limit, action list or patient frame range is hardcoded. Editing a
`resources/scenarios/<id>/scenario.json` changes the UI with zero code edits — `gameReducer.test.ts` asserts exactly that.

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
array order**. A top-level `environment` adds an `__environment__` plate under
everything. For `scenario_1` (no `environment`; its room is the Environmental
action at −1) that is Environmental → patient → Caregiver → Force IV →
Restraint. For `scenario_3` it is environment → `door_closed` (−2) →
`staff_present` (−1) → patient: the door layer repaints most of the staff's
pixels, so it must sit underneath when both are up.

Every path is `/visuals/<scenario id>/<file>` (`visualSrc`). Patient frames are
`patient_<n>.<patient_frame_ext>` (default `png`) for `n` in
`0..point_bar.max`; `patientFrameSources(scenario)` is what `SceneStage`
preloads. Other images use their full file names, so PNG/JPG and WebP coexist.

Each layer's src is `active` when the action is in `state_update.active_actions`
and `inactive` otherwise; `null` means the layer renders nothing (Force IV and
Restraint have no inactive art). `SceneLayer` keeps every src it has shown
mounted and cross-fades opacity rather than swapping one `<img>`'s src, and
`SceneStage` pins one fixed aspect (`aspect-stage`, the art's native 1196×880)
that all layers absolutely fill.

`state_update.status` is stored but deliberately not rendered.

## Debrief checklist

`selectChecklist` lists every action in scenario order. A conditional action
(`requires`) shows its with-KEY value plus a muted `(<without> without <KEY>)`
note; an END SIMULATION action shows `END` and is `found-bad` once taken. The
END debrief itself arrives as `game_over.reason`, which `EndScreen` already
renders. Inactivity charges are not actions and never appear here.

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
