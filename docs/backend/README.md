# Backend

Everything server-side in the BRIDGE rewrite: the control-plane Lambda, the voice runtime, and the infra that provisions them. Start here, then follow the map into the doc that matches the task.

## Trees

| Tree | What it is | Runs on |
|---|---|---|
| `api/` | Thin FastAPI control plane: `/scenario` + the voice signaling router. Deliberately **pipecat-free**. Ships as a container image (`api/Dockerfile.api`) served by uvicorn under the Lambda Web Adapter; the Mangum `handler` is a zip fallback. | Lambda (container image, LWA) |
| `runtime/voice_kit/` | Vendored voice pipeline kit — control plane (router, config, KVS, errors) + pipeline (pipecat, providers, processors). | Lambda (control-plane half) + container (pipeline half) |
| `runtime/bridge/` | BRIDGE's own runtime code: the game engine and the wire contract. Container-only. | AgentCore container |
| `amplify/` | Amplify Gen 2 / CDK infra: `backend.ts` (API Lambda + Function URL + voice runtime), `constants.ts` (deploy-time config), `voice-runtime.ts` (the vendored kit module). | Deploy time |
| `resources/` | Scenario config + prompts. Shared with the legacy app; COPYed to `/app/resources` in **both** images. | Both |
| `runtime/evals/` | Manual, network-hitting prompt evals (referee eval + patient probe). Never run by CI; still linted. See `prompts.md`. | Local |
| `scripts/` | `gen_event_types.py` (event-contract codegen), `local_voice_smoke.py` (local-mode WebRTC smoke), `make_transparent.py` (visual asset tool). | Local / CI |

## Doc map

| Doc | Read it for |
|---|---|
| `local-dev.md` | `BRIDGE_LOCAL=1`: the one-machine dev loop, what the flag changes, the Tier-1 smoke |
| `prompts.md` | The referee + patient prompts, what the model is (and is not) shown, the manual evals |
| `deployment.md` | Environment topology, the AZ record, secrets, the deploy runbook, cost |
| `voice-kit/00-architecture.md` | Topology, the pipeline chain, the two session ids, extension points |
| `voice-kit/01-integration-guide.md` | How the halves wire together; the verification gates |
| `voice-kit/02-configuration.md` | Every env var, provider tables, the SSM secrets mechanism |
| `voice-kit/03-infrastructure.md` | VPC / KVS / AgentCore / IAM, the `addVoiceRuntime` worked example |
| `voice-kit/04-deploy-runbook.md` | First deploy, post-deploy checklist, symptom → cause table |
| `voice-kit/05-gotchas.md` | The hard-won lessons — read before touching infra or the pipeline |
| `../frontend/voice-client.md` | The browser half of the same pipeline |

## Frozen contracts

**Data-channel event envelope (v1).** Pydantic models in `runtime/bridge/events.py`; TypeScript generated into `web/src/voice/gameEvents.gen.ts`. Every message carries `v: 1` and a `type` discriminator.

| type | payload |
|---|---|
| `transcript_update` | `role: "student"\|"patient"`, `content`, `timestamp` |
| `state_update` | `escalation`, `max`, `active_actions: string[]`, `status` |
| `action_detected` | `action_type`, `desc`, `point_change` |
| `timer` | `elapsed`, `limit` |
| `game_over` | `status: "success"\|"fail"`, `reason` |

Transcript roles are domain roles (`student`/`patient`) — deliberately not `voice_kit.types.TranscriptMessage`'s chat-native `user`/`assistant`, which is mapped at the boundary.

Regenerate the TS after any change to the models (CI fails on drift):

```bash
python3 scripts/gen_event_types.py           # write
python3 scripts/gen_event_types.py --check   # verify
```

**Invoker interface** (control plane → runtime): `voice_kit.Invoker`, both methods async — `await invoker.signal(session_id, runtime_session_id, sdp, type="offer") -> dict` and `await invoker.end(session_id, runtime_session_id) -> dict`. Selected by `VOICE_INVOKER` via `get_invoker()`: `AgentCoreInvoker` (boto3 `invoke_agent_runtime` in `asyncio.to_thread`, hides the streaming-body response) or `LocalInvoker` (aiohttp POST to `{VOICE_RUNTIME_URL}/invocations`, same payloads and same error contract; implied by `BRIDGE_LOCAL=1` — see `local-dev.md`). `create_voice_router(invoker=...)` accepts an override for tests/custom backends. Teardown is router-owned: `/end` with a `SessionEndRequest` body (`{runtime_session_id}`) best-effort invokes `end()` (payload `{"session_id", "action": "end"}`) before the `on_end` hook; no body skips it. The runtime entrypoint dispatches on `payload.action`: `"signal"` (default) | `"end"`.

## Game engine (`runtime/bridge/`)

The rules layer that turns the kit's pipeline into the simulation. One `GameSession` per `session_id`, in memory, for the container's life — no database, and no locks (single asyncio loop).

| Module | Owns |
|---|---|
| `config.py` | Env reads + the cached scenario / referee-prompt / patient-prompt / patient-case loaders |
| `session.py` | `GameSession` (escalation, action states, transcript, clock origin) + the `_sessions` registry and its sweep |
| `referee.py` | `RefereeProcessor` — scores each student utterance, applies the scenario's point values, emits the turn's events |
| `emitter.py` | `GameEvents` — the v1 envelope helpers; `transcript_event` maps the sink's turns (patient only) |
| `patient.py` | Patient prompt assembly (template + case file), voice mapping, the per-turn escalation marker — see `prompts.md` |
| `timer.py` | The 1 Hz clock and the post-`game_over` grace reaper |
| `app.py` | The uvicorn/Docker target: registers every hook and re-exports the kit's app |
| `events.py` | The frozen v1 wire contract (above) |

**One turn**, in order: STT → referee (`transcript_update{student}` → `action_detected`×N → `state_update` → `game_over`?) → patient LLM (gated off once the game is over) → TTS → sink (`transcript_update{patient}`). That order is guaranteed by construction — the referee emits directly over the ordered data channel and finishes before the frame reaches the patient LLM, while `timer` ticks interleave freely. The connect-time authoritative `state_update` precedes everything.

**Status vocabulary** is `active | success | fail` — `status` on both `state_update` and `game_over`, and what the SPA switches on. A game ends when escalation reaches the scenario's goal (success), its maximum (fail), or the clock runs out (fail); the reaper then cancels the pipeline after `GAME_GRACE_SECONDS`. The pipeline's own idle backstop is held outside that window — `config.idle_timeout_for()` derives `SessionContext.idle_timeout_seconds` as `time_limit + GAME_GRACE_SECONDS + IDLE_TIMEOUT_MARGIN_SECONDS`, because pipecat cancels the pipeline on idle and one firing mid-game would take the data channel down before `game_over` (`voice-kit/05-gotchas.md` #35).

## Packaging

`runtime/pyproject.toml` publishes **`voice_kit` only**, with just its pipecat-free core deps (`fastapi`, `pydantic`, `pydantic-settings`, `boto3`, `aiohttp`) — that is what makes the control plane installable into the Lambda without dragging pipecat in. `runtime/bridge/` is not packaged: it reaches the container through `Dockerfile.voice`, whose dependency source is `runtime/requirements-voice.txt` (`COPY` + `PYTHONPATH=/app`, no pip-install of the package).

`api/` consumes the package through the `./runtime` line in `api/requirements.txt` — the single source of truth for CI and local dev (`pip install -r api/requirements.txt` from the repo root). `api/Dockerfile.api` installs the same set in layered form: the third-party deps first (that file minus the tooling pins and the `./runtime` line, plus the kit's own `pyproject.toml` dependency list), then the kit itself with `--no-deps`. `api/` and its tests do a plain `import voice_kit` against the installed package.

## Commands

```bash
# The whole stack on localhost, zero AWS calls (see local-dev.md).
npm run dev                                    # SPA :5173 + API :8000 + runtime :8080
.venv/bin/python scripts/local_voice_smoke.py  # Tier-1 acceptance smoke

# Two invocations, not one: `api/` tests the INSTALLED voice_kit package while
# runtime/ tests the tree, and collecting both at once lets the installed copy
# shadow the repo. CI runs them separately for the same reason.
python3 -m pytest api/tests
python3 -m pytest runtime/tests
ruff check api runtime                         # lint (config: root ruff.toml)
ruff format --check api runtime                # format
python3 -m compileall runtime/voice_kit runtime/bridge api

# Pipecat-free gate — the second command must FAIL (proves the venv is clean):
python3 -m venv /tmp/cp && /tmp/cp/bin/pip install ./runtime
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
