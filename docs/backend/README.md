# Backend

Everything server-side in the BRIDGE rewrite: the control-plane Lambda, the voice runtime, and the infra that provisions them. Start here, then follow the map into the doc that matches the task.

## Trees

| Tree | What it is | Runs on |
|---|---|---|
| `api/` | Thin FastAPI control plane: `GET /scenarios` (picker list) + `GET /scenario/{id}` (the whitelisted config; 404 on an unknown id) + `POST /sessions` (creates the session record and issues the session id; see [Session records](#session-records)) + the voice signaling router. Every scenario loads at import, so a broken bundle fails the cold start. Deliberately **pipecat-free**. Ships as a container image (`api/Dockerfile.api`) served by uvicorn under the Lambda Web Adapter; the Mangum `handler` is a zip fallback. | Lambda (container image, LWA) |
| `runtime/voice_kit/` | Vendored voice pipeline kit — control plane (router, config, KVS, errors) + pipeline (pipecat, providers, processors). | Lambda (control-plane half) + container (pipeline half) |
| `runtime/bridge/` | BRIDGE's own runtime code: the game engine and the wire contract. Container-only. | AgentCore container |
| `amplify/` | Amplify Gen 2 / CDK infra: `backend.ts` (API Lambda + Function URL + voice runtime), `constants.ts` (deploy-time config), `voice-runtime.ts` (the vendored kit module). | Deploy time |
| `resources/` | `scenarios/<id>/{scenario.json, patient.txt, patient.json}` — one folder per scenario, the folder names being the allow-list — plus the shared `referee.txt`. COPYed to `/app/resources` in **both** images. | Both |
| `docs/scenarios/` | Authored source content for each scenario (intro, point bar, action table with scene changes): the source of truth that `resources/` configs, patient personas and the visual shot lists are transcribed from. Not shipped. | — |
| `runtime/evals/` | Manual, network-hitting prompt evals (referee eval + patient probe). Never run by CI; still linted. See `prompts.md`. | Local |
| `scripts/` | `gen_event_types.py` (event-contract codegen), `local_voice_smoke.py` (local-mode WebRTC smoke), `cloud_voice_probe.py` (the deployed-stack counterpart, and the post-deploy gate). `visuals/`: the scene-art pipeline — Nano Banana generation, clean-plate layer extraction, masters in S3, web-asset export (see `visual-pipeline.md`). | Local / CI |

## Doc map

| Doc | Read it for |
|---|---|
| `local-dev.md` | `BRIDGE_LOCAL=1`: the one-machine dev loop, what the flag changes, the Tier-1 smoke |
| `prompts.md` | The referee + patient prompts, what the model is (and is not) shown, the manual evals |
| `deployment.md` | Environment topology, secrets, the deploy runbook, cost |
| `visual-pipeline.md` | The scene-art tooling (`scripts/visuals/`): shot lists, generation, extraction, the masters bucket + manifest, export, costs |
| `voice-kit/00-architecture.md` | Topology, the pipeline chain, the two session ids, extension points |
| `voice-kit/01-integration-guide.md` | How the halves wire together; the verification gates |
| `voice-kit/02-configuration.md` | Every env var, provider tables, the SSM secrets mechanism |
| `voice-kit/03-infrastructure.md` | KVS / AgentCore / IAM / managed PUBLIC network, the `addVoiceRuntime` worked example |
| `voice-kit/04-deploy-runbook.md` | First deploy, post-deploy checklist, symptom → cause table |
| `voice-kit/05-gotchas.md` | The hard-won lessons — read before touching infra or the pipeline |
| `../frontend/voice-client.md` | The browser half of the same pipeline |

## Frozen contracts

**Data-channel event envelope (v1).** Pydantic models in `runtime/bridge/events.py`; TypeScript generated into `web/src/voice/gameEvents.gen.ts`. Every message carries `v: 1` and a `type` discriminator.

| type | payload |
|---|---|
| `transcript_update` | `role: "student"\|"patient"`, `content`, `timestamp` |
| `state_update` | `escalation`, `max`, `active_actions: string[]`, `status` |
| `action_detected` | `action_type`, `desc`, `point_change`, `repeat` (default `false`) |
| `timer` | `elapsed`, `limit` |
| `game_over` | `status: "success"\|"fail"`, `reason` |

**Inbound (client → runtime).** The browser sends one message back over the same channel, kept out of the `GameEvent` union in `CLIENT_MODELS` (and generated to TS after it):

| type | payload |
|---|---|
| `student_text` | `content` — a typed student turn (Text mode) |

`bridge/typed_input.py` validates it in `transport.event_handler("on_app_message")` (pipecat consumes its own `type: "signalling"` messages first). The content is stripped, and anything empty, over 1000 characters, malformed or `v ≠ 1` is logged at debug and dropped. The runtime does not know a session's mode, so it accepts typed text from any session.

Transcript roles are domain roles (`student`/`patient`) — deliberately not `voice_kit.types.TranscriptMessage`'s chat-native `user`/`assistant`, which is mapped at the boundary.

Regenerate the TS after any change to the models (CI fails on drift):

```bash
python scripts/gen_event_types.py           # write
python scripts/gen_event_types.py --check   # verify
```

**Invoker interface** (control plane → runtime): `voice_kit.Invoker`, both methods async — `await invoker.signal(session_id, runtime_session_id, sdp, type="offer") -> dict` and `await invoker.end(session_id, runtime_session_id) -> dict`. Selected by `VOICE_INVOKER` via `get_invoker()`: `AgentCoreInvoker` (boto3 `invoke_agent_runtime` in `asyncio.to_thread`, hides the streaming-body response) or `LocalInvoker` (aiohttp POST to `{VOICE_RUNTIME_URL}/invocations`, same payloads and same error contract; implied by `BRIDGE_LOCAL=1` — see `local-dev.md`). `create_voice_router(invoker=...)` accepts an override for tests/custom backends. Teardown is router-owned: `/end` with a `SessionEndRequest` body (`{runtime_session_id}`) best-effort invokes `end()` (payload `{"session_id", "action": "end"}`) before the `on_end` hook; no body skips it. The runtime entrypoint dispatches on `payload.action`: `"signal"` (default) | `"end"`.

## Game engine (`runtime/bridge/`)

The rules layer that turns the kit's pipeline into the simulation. One `GameSession` per `session_id`, in memory, for the container's life, with no locks (single asyncio loop). That memory is the only live game state: the session record in DynamoDB is written as the game runs but never read back to resume one.

| Module | Owns |
|---|---|
| `config.py` | Env reads, the scenario allow-list (`scenario_ids()`), `parse_session_id()`, and the per-scenario cached loaders (scenario / patient prompt / patient case, keyed by id) plus the shared referee prompt |
| `session.py` | `GameSession` (escalation, action states, transcript, clock origin, inactivity clock, pending END) + the `_sessions` registry and its sweep |
| `referee.py` | `RefereeProcessor` — scores each student utterance, applies the scenario's point values (`requires`-free actions first), emits the turn's events |
| `emitter.py` | `GameEvents` — the v1 envelope helpers; `transcript_event` maps the sink's turns (patient only) |
| `patient.py` | Patient prompt assembly (template + case file), voice mapping, the per-turn escalation marker — see `prompts.md` |
| `timer.py` | The 1 Hz clock (which also charges inactivity) and the post-`game_over` grace reaper |
| `recorder.py` | `SessionRecorder` — turns game events into session-record updates on a bounded per-session queue, drained by a background writer (see [Session records](#session-records)) |
| `store.py` | The runtime's record backends (pipecat-free): `DynamoSessionTable` (one conditional `UpdateItem`), `FileSessionTable` (local), `NullSessionTable`, and `get_session_table()` |
| `typed_input.py` | Text mode: `parse_student_text` (the inbound `student_text` check) and `inject_student_text` (queues the typed turn as a user `TranscriptMessageFrame`, `source="text"`) |
| `app.py` | The uvicorn/Docker target: registers every hook (including the `on_app_message` handler for typed turns) and re-exports the kit's app |
| `events.py` | The frozen v1 wire contract (above), outbound and inbound |

**Which scenario.** `POST /sessions` issues `session_id = <scenario_id>--<uuid>` (the SPA no longer mints its own) and `provide_context` resolves the scenario with `parse_session_id` (split on the first `--`, checked against the allow-list). A missing or unknown prefix raises; there is no fallback scenario. This is a **prototype shortcut**: the kit hands `provide_context` only the session id and `SignalRequest` has no metadata field, so the id is the one channel there is. The cleaner fix is a metadata passthrough in `voice_kit`.

**Mechanics**, all optional and all read from `scenario.json`:

| Key | Where | Effect |
|---|---|---|
| **Helpful actions count once** | always on | A helpful (effective delta < 0) action is credited once per session: a repeat emits `action_detected{point_change: 0, repeat: true}`, keeps its layer lit, and does not restart the inactivity clock. `GameSession.credited` keeps the most negative delta paid per type, so a repeat after its `requires` KEY earns the difference (a top-up, `repeat: false`). Escalating and zero-point actions apply every time. |
| `requires` + `point_change_without` | action | Worth `point_change` once the named KEY action is in `actions_ever_taken`, else `point_change_without`. Within one turn `requires`-free actions apply first, so a KEY said in the same utterance counts. `action_detected.point_change` carries the effective delta. |
| `ends_simulation` + `debrief` | action (`point_change: 0`) | Ends the game as `fail` with `reason = debrief` on the turn's `check_terminal`, ahead of any success. The referee sees it as `escalates: true`. |
| `inactivity: {seconds, point_change}` | top level | The timer charges `point_change` every `seconds` without a helpful (effective delta < 0) action, counted from game start; each charge emits `state_update` and can end the game at max. Not an action: never sent to the referee or shown in the checklist. |
| `environment`, `intro_image`, `patient_frame_ext` | top level | Client-only visuals (see `../frontend/app-shell.md`). |

**One turn**, in order: STT → referee (`transcript_update{student}` → `action_detected`×N → `state_update` → `game_over`?) → patient LLM (gated off once the game is over) → TTS → sink (`transcript_update{patient}`). That order is guaranteed by construction — the referee emits directly over the ordered data channel and finishes before the frame reaches the patient LLM, while `timer` ticks interleave freely. The connect-time authoritative `state_update` precedes everything.

**Typed turns** (Text mode) enter at the STT output: `inject_student_text` queues a user `TranscriptMessageFrame` with `source="text"` at the pipeline source with `PipelineWorker.queue_frame`, and VAD and STT forward it untouched. From the referee on it is the same code as speech, so scoring, ordering, the patient's spoken reply and the record are identical (`test_pipeline_seams.py` pins the pass-through). Once the game is over a typed turn is not queued: it is echoed as `transcript_update{student}` and recorded unscored, which is what the referee would have done with it. The browser's text connection is receive-only for audio (`../frontend/voice-client.md`), and the runtime's `TransportParams` stay the same for both modes (`voice-kit/05-gotchas.md` #39).

**Status vocabulary** is `active | success | fail` — `status` on both `state_update` and `game_over`, and what the SPA switches on. A game ends when escalation reaches the scenario's goal (success), its maximum (fail), or the clock runs out (fail); the reaper then cancels the pipeline after `GAME_GRACE_SECONDS`. The pipeline's own idle backstop is held outside that window — `config.idle_timeout_for()` derives `SessionContext.idle_timeout_seconds` as `time_limit + GAME_GRACE_SECONDS + IDLE_TIMEOUT_MARGIN_SECONDS`, because pipecat cancels the pipeline on idle and one firing mid-game would take the data channel down before `game_over` (`voice-kit/05-gotchas.md` #35).

## Session records

Every case a student plays is one DynamoDB item in `BridgeSessions` (partition key `session_id`), updated as the game runs. The item *is* the record, so one `GetItem` reads a whole session. Sessions are listed from the `by_created` GSI (partition `kind` = `"session"`, sort `created_at`), which projects only the summary attributes (`SESSION_SUMMARY_ATTRIBUTES` in `amplify/constants.ts`).

**Who writes what.**

| Writer | Does | Never |
|---|---|---|
| Control plane (`api/sessions.py`) | `POST /sessions {netid, scenario_id, mode}`: normalises the NetID (trim, lowercase, drop `@…`, then `^[a-z]+[0-9]*$` at 2–20 chars), checks the scenario and mode (422 otherwise), mints the id and `PutItem`s the item with `status: started` | — |
| Runtime (`bridge/recorder.py`) | `UpdateItem` by session id, conditional on `attribute_exists(session_id)` | Create an item, read one, or see the NetID |

The NetID stays in the control plane. The runtime only knows the session id, so personal data stays out of the voice container, its logs, AgentCore session names and signaling. A write for an id the API never issued fails its condition, is logged, and the game carries on. `POST /sessions` is unauthenticated, like the `/voice` endpoints.

**When the runtime writes.** On connect (`status: active`, `started_at`, the bounds, the first timeline point; once per `GameSession`, so a reconnect does not reset it). After each scored student turn, from `RefereeProcessor.score_turn` once its events are out. After each patient turn, from `store_turn`. On each inactivity charge. At game over, from the referee or the timer. On teardown of a still-active session (`abandoned`). That is about two writes per turn.

**Off the hot path.** Each recorder method only builds an update and `put_nowait`s it on a per-session `asyncio.Queue` (bounded at 256), which costs microseconds. A writer task drains it in order and runs boto3 in `asyncio.to_thread`. The recorder fails open, like the referee: a failed or slow write is logged and dropped, and so is an update that overflows the queue. `on_session_end` drains the queue for up to 2 s and then cancels the writer.

**Item schema** (the contract every reader relies on):

```jsonc
{
  "session_id": "scenario_3--<uuid>", "kind": "session", "created_at": "ISO",
  "netid": "abc123", "scenario_id": "scenario_3", "scenario_title": "…",
  "mode": "voice" | "text", "user_agent": "…",            // header, ≤ 256 chars
  "status": "started" | "active" | "success" | "fail" | "abandoned",
  "end_reason": null | "goal" | "max" | "timeout" | "end_action" | "abandoned",
  "reason": null | "…",                                     // game_over reason / END debrief
  "start_escalation": 5, "escalation": 5, "max_escalation": 10, "goal": 0,
  "time_limit_s": 300, "started_at": "ISO", "ended_at": "ISO", "duration_s": 212.4,
  "updated_at": "ISO",
  "helpful_count": 0, "escalating_count": 0,                // actions with effective delta < 0 / > 0
  "turns": [
    {"role": "student", "t": 12.3, "at": "ISO", "content": "…", "source": "voice",
     "actions": [{"type": "…", "point_change": -3, "repeat": false}], "escalation_after": 2},
    {"role": "patient", "t": 15.9, "at": "ISO", "content": "…"}
  ],
  "timeline": [{"t": 0, "escalation": 5, "cause": "start"},
               {"t": 12.3, "escalation": 2, "cause": "turn"}]   // cause: start | turn | inactivity
}
```

`t` is seconds since game start (the on-screen clock) and `at` is wall-clock UTC. The gap between a student turn's `t` and the next patient turn's is the response latency. A student turn after the game has ended is still recorded, with `actions: []`. The timeline gets a point only when the escalation changes. `source` comes from `TranscriptMessage.source` (`voice` unless the turn was typed). Only text is stored; audio never is.

**End reasons.** `goal`, `max` and `timeout` are the three game endings. `end_action` is an END SIMULATION action, and `reason` holds its debrief. `abandoned` is a teardown while still active. There is no in-game End button, so there is no "ended early".

**Abandonment is derived on read.** A closed tab fires neither `/end` nor the reaper, so the runtime often never gets to write `abandoned`. Readers must therefore treat an `active` item older than `started_at + time_limit_s + 300 s` as abandoned, and a `started` item older than `created_at + 1 h` as never connected. Because the runtime only writes on events, that derivation is deterministic.

**Stores.** Both sides pick a backend the same way: under `BRIDGE_LOCAL`, a JSON file per session under `SESSIONS_LOCAL_DIR` (default `<repo>/.local-data/sessions`, gitignored; see `local-dev.md`); with `SESSIONS_TABLE` set, DynamoDB; with neither, a no-op (runtime) or in-memory dict (API) for unit tests. `api/session_store.py` also carries `get` and `list_summaries` (all pages of the GSI, newest first) for readers; no endpoint serves them yet. Table, retention and manual deletion: `deployment.md`.

## Adding a scenario

1. Write the source content in `docs/scenarios/<id>.md` (intro, point bar, action table with scene changes).
2. Generate and export the art with the scene-art pipeline ([`visual-pipeline.md`](visual-pipeline.md)) into `web/public/visuals/<id>/`: `environment`, `patient_0..max`, the action layers, `intro`.
3. Create `resources/scenarios/<id>/`:
   - `scenario.json` — `id` (= folder name), `title`, `intro_image`, `intro`, `goal`, `speech`, `time_limit`, `point_bar`, `actions`, plus any of `environment`, `patient_frame_ext`, `inactivity`. Visual scene changes are action layers (`persist: true`, a negative `layer` to sit below the patient; equal layers stack in `actions` order). A KEY's dependants use `requires`; an END SIMULATION row uses `ends_simulation` + `debrief`; a "no action over time" row becomes `inactivity`, not an action.
   - `patient.txt` — the persona: the setting, an escalation-band table carrying the non-visual scene changes, and the standing rules (spoken words only, no clinical history). See `prompts.md`.
   - `patient.json` — the case file (`demographics`, `behavior`, `chief_concern`, `free_information`, `locked_information`).
4. Nothing else: the folder is discovered by both the API and the runtime, the referee's enum and `escalates` flags are generated, and the picker lists it. `python -m pytest` checks the new config's `requires` / `debrief` invariants and persona rules.

## Packaging

`runtime/pyproject.toml` publishes **`voice_kit` only**, with just its pipecat-free core deps (`fastapi`, `pydantic`, `pydantic-settings`, `boto3`, `aiohttp`) — that is what makes the control plane installable into the Lambda without dragging pipecat in. `runtime/bridge/` is not packaged: it reaches the container through `Dockerfile.voice`, whose dependency source is `runtime/requirements-voice.txt` (`COPY` + `PYTHONPATH=/app`, no pip-install of the package).

`api/` consumes the package through the `./runtime` line in `api/requirements.txt` — the single source of truth for CI and local dev (`pip install -r api/requirements.txt` from the repo root). `api/Dockerfile.api` installs the same set in layered form: the third-party deps first (that file minus the tooling pins and the `./runtime` line, plus the kit's own `pyproject.toml` dependency list), then the kit itself with `--no-deps`. `api/` and its tests do a plain `import voice_kit` against the installed package.

## Commands

```bash
# The whole stack on localhost, zero AWS calls (see local-dev.md).
npm run dev                                    # SPA :5173 + API :8000 + runtime :8080
.venv/bin/python scripts/local_voice_smoke.py  # Tier-1 acceptance smoke

# The deployed stack — real KVS TURN, real AgentCore session. Never a substitute
# for the other direction either: run it after every backend deploy.
python scripts/cloud_voice_probe.py

# One combined run, same as CI: the root pytest.ini puts runtime/ on the path
# and collects api/tests + runtime/tests (the kit is installed editable, so
# nothing can import a stale copy).
python -m pytest
ruff check api runtime                         # lint (config: root ruff.toml)
ruff format --check api runtime                # format
python -m compileall runtime/voice_kit runtime/bridge api

# The scene-art tooling (its own venv; see visual-pipeline.md)
python -m pytest scripts/visuals/tests
ruff check scripts/visuals && ruff format --check scripts/visuals

# Pipecat-free gate — the second command must FAIL (proves the venv is clean):
python -m venv /tmp/cp && /tmp/cp/bin/pip install ./runtime
/tmp/cp/bin/python -c "import voice_kit; voice_kit.create_voice_router()"
/tmp/cp/bin/python -c "import pipecat"

# Both images (context = repo root; see the root .dockerignore)
docker build --platform linux/arm64 -f runtime/Dockerfile.voice -t bridge-voice .
docker run --rm -p 8080:8080 bridge-voice && curl localhost:8080/ping

docker build --platform linux/arm64 -f api/Dockerfile.api -t bridge-api .
docker run --rm -p 8000:8000 -e ALLOWED_ORIGINS=http://localhost:5173 bridge-api
curl localhost:8000/health

# Infra
npm ci && npx tsc --noEmit -p amplify && npm test
```

CI runs the same checks in `.github/workflows/backend-tests.yml` and `amplify-tests.yml`.
