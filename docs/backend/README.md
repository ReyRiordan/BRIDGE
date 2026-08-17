# Backend

Everything server-side in the BRIDGE rewrite: the control-plane Lambda, the voice runtime, and the infra that provisions them. Start here, then follow the map into the doc that matches the task.

## Trees

| Tree | What it is | Runs on |
|---|---|---|
| `api/` | Thin FastAPI control plane (Mangum): `/scenario` + the voice signaling router. Deliberately **pipecat-free**. | Lambda |
| `runtime/voice_kit/` | Vendored voice pipeline kit — control plane (router, config, KVS, errors) + pipeline (pipecat, providers, processors). | Lambda (control-plane half) + container (pipeline half) |
| `runtime/bridge/` | BRIDGE's own runtime code: the game engine and the wire contract. Container-only. | AgentCore container |
| `amplify/` | Amplify Gen 2 / CDK infra: `backend.ts` (API Lambda + Function URL + voice runtime), `constants.ts` (deploy-time config), `voice-runtime.ts` (the vendored kit module). | Deploy time |
| `resources/` | Scenario config + prompts. Shared with the legacy app; COPYed into the runtime image at `/app/resources`. | Both |
| `scripts/` | `gen_event_types.py` (event-contract codegen), `make_transparent.py` (visual asset tool). | Local / CI |

## Doc map

| Doc | Read it for |
|---|---|
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

**Invoker interface** (control plane → runtime): `Invoker.signal(session_id, runtime_session_id, sdp) -> dict` and `Invoker.end(session_id, runtime_session_id) -> dict`. Implementations: `AgentCoreInvoker` (boto3 `invoke_agent_runtime`, hides the streaming-body response) and `LocalInvoker` (localhost `/invocations`). The runtime entrypoint dispatches on `payload.action`: `"signal"` (default) | `"end"`.

## Packaging

`runtime/pyproject.toml` publishes **`voice_kit` only**, with just its pipecat-free core deps (`fastapi`, `pydantic`, `pydantic-settings`, `boto3`, `aiohttp`) — that is what makes the control plane installable into the Lambda without dragging pipecat in. `runtime/bridge/` is not packaged: it reaches the container through `Dockerfile.voice`, whose dependency source is `runtime/requirements-voice.txt` (`COPY` + `PYTHONPATH=/app`, no pip-install of the package).

How `api/` consumes the package at Lambda bundling time (expected: `pip install ./runtime`) and how tests set up the path is decided in **[Rewrite C]**.

## Commands

```bash
python3 -m pytest api/tests runtime/tests      # tests
ruff check api runtime                         # lint (config: root ruff.toml)
ruff format --check api runtime                # format
python3 -m compileall runtime/voice_kit runtime/bridge api

# Pipecat-free gate — the second command must FAIL (proves the venv is clean):
python3 -m venv /tmp/cp && /tmp/cp/bin/pip install ./runtime
/tmp/cp/bin/python -c "import voice_kit; voice_kit.create_voice_router()"
/tmp/cp/bin/python -c "import pipecat"

# Runtime image (context = repo root; see the root .dockerignore)
docker build --platform linux/arm64 -f runtime/Dockerfile.voice -t bridge-voice .
docker run --rm -p 8080:8080 bridge-voice && curl localhost:8080/ping

# Infra
npm ci && npx tsc --noEmit -p amplify && npm test
```

CI runs the same checks in `.github/workflows/backend-tests.yml` and `amplify-tests.yml`.
