# Integration guide

How BRIDGE wires the voice kit together. Read `00-architecture.md` first for the shape; `05-gotchas.md` before your first deploy.

The kit is a voice-to-voice pipeline — **browser WebRTC → Bedrock AgentCore → pipecat (STT → LLM → TTS)** — extracted from a production deployment and now vendored into this repo:

- **STT**: Amazon Transcribe Streaming (default) or Together AI (Parakeet)
- **LLM**: OpenRouter or AWS Bedrock (bedrock-mantle, SigV4) — env-switchable
- **TTS**: Amazon Polly (generative streaming, default) or Inworld
- **Transport**: WebRTC over KVS-managed TURN (relay-only), signaling proxied through `api/`
- **Hosting**: AgentCore Runtime (ARM64 container), infra as an Amplify Gen 2 / CDK module

Domain logic is not baked in: the kit exposes extension points (a per-session context provider, a transcript sink, and auth/lifecycle hooks on the signaling router) that BRIDGE's game engine fills in.

## Where the code lives

| Piece | Path |
|---|---|
| Package (control plane + pipeline) | `runtime/voice_kit/` |
| Container image + deps | `runtime/Dockerfile.voice`, `runtime/requirements-voice.txt` |
| BRIDGE runtime code (game engine, events) | `runtime/bridge/` |
| Infra module | `amplify/voice-runtime.ts` |
| Browser client | `web/src/voice/` (see `docs/frontend/voice-client.md`) |

`voice_kit` is imported as plain `voice_kit.*`; the container sets `PYTHONPATH=/app`, and `runtime/pyproject.toml` packages it (control-plane deps only) for the API Lambda.

## What BRIDGE must supply

- [ ] A **system prompt** — the context provider builds one per session; the patient persona itself is still a placeholder ([Rewrite E])
- [ ] **Auth** on the three endpoints (`authorize` hook and/or FastAPI `dependencies`) — the defaults are open. Currently a documented `TODO(auth)` no-op at `authorize` in `api/main.py` — the `/signal` path is deliberately open until auth lands
- [ ] A **session id** concept (any string the app can re-fetch by)
- [x] A **transcript sink** for server-side transcripts (`set_transcript_handler`) — in-memory, `runtime/bridge/app.py`
- [x] Session **state transitions** (`on_start` / `on_end`) — plus the runtime-side session hooks (clock + grace reaper)
- [ ] Verified **AgentCore AZ letters** for the account (gotcha #4)
- [ ] Secrets in **SSM** for whichever providers are enabled

## Step 1 — control plane (API host)

Deps: `fastapi`, `boto3`, `pydantic>=2`, `pydantic-settings`, `aiohttp` (no pipecat — the control-plane surface is deliberately pipecat-free).

```python
from fastapi import Depends, HTTPException, Request
from voice_kit import create_voice_router, register_exception_handlers, TranscriptMessage

async def authorize(request: Request, session_id: str) -> None:
    user = await my_auth(request)                      # your auth
    session = my_get_session(session_id)               # your store
    if session.owner != user.id:
        raise HTTPException(403, "Not your session")

async def on_start(session_id: str) -> None:
    my_mark_started(session_id)                        # idempotent! /start re-runs on retry

async def on_end(session_id: str) -> list[TranscriptMessage]:
    my_mark_ended(session_id)
    return my_read_transcript(session_id)              # AFTER the flip (gotcha #23)

app.include_router(create_voice_router(
    authorize=authorize, on_start=on_start, on_end=on_end,
    # invoker=my_invoker,                              # default: get_invoker() per VOICE_INVOKER
    # dependencies=[Depends(get_current_user)],        # FastAPI-native auth also works
    prefix="/voice",
))
register_exception_handlers(app)                       # 502s with CORS headers (gotcha #22)
```

Env needed here: `VOICE_RUNTIME_ARN`, `KVS_CHANNEL_NAME`, `AWS_REGION` (infra injects the first two).

## Step 2 — runtime (AgentCore container)

Write a tiny wrapper module that registers your hooks and re-exports the app:

```python
# runtime/bridge/app.py  (the uvicorn target in Dockerfile.voice)
from voice_kit import SessionContext, set_context_provider, set_processor_factory
from voice_kit.runtime import app, end_session  # noqa: F401  — `app` is the target

async def provide_context(session_id: str) -> SessionContext:
    # Pointer + re-fetch. BRIDGE's store is the container's own memory: a
    # reconnect onto the same warm container gets the same GameSession back.
    # (A provider doing real I/O must wrap it — this runs on the packet-pump loop.)
    scenario = load_scenario()
    session = get_or_create_session(session_id, scenario)
    return SessionContext(
        system_prompt=build_patient_prompt(scenario),
        voice=build_voice_config(scenario),          # scenario["speech"], incl. speed
        initial_history=list(session.transcript),    # resume support
        time_limit_seconds=scenario["time_limit"],
        metadata={"game": session},                  # hoisted to PipelineContext.game
    )

def build_game_processors(args):
    session = args.session_context.metadata["game"]
    events = GameEvents(args.session_id, args.emit)
    return [
        STTProcessor(...),
        RefereeProcessor(session=session, events=events, ...,
                         on_game_over=lambda: start_reaper(session.session_id)),
        LLMProcessor(..., turn_gate=lambda: session.status == "active",
                     turn_context=lambda: turn_context(session)),
        TTSProcessor(voice=args.voice),
        EventSinkProcessor(..., transcript_event=transcript_event),
    ]

set_context_provider(provide_context)
set_processor_factory(build_game_processors)
set_transcript_handler(store_turn)      # appends to session.transcript
set_session_start_hook(on_session_start)  # starts the clock; state_update on connect
set_session_end_hook(on_session_end)      # stop timer, disarm reaper, drop session
set_pipeline_canceller(end_session)        # what the grace reaper calls
```

`runtime/bridge/` is COPYed into the image and `Dockerfile.voice`'s CMD is `bridge.app:app` — importing that module is what registers the hooks above. The full engine (referee, session registry, clock, reaper) is documented in `00-architecture.md`.

## Step 3 — infra

`amplify/voice-runtime.ts` is in place; `backend.ts` calls `addVoiceRuntime(...)` with verified AZs, the config constants, SSM secret names/prefixes, and `extraRuntimePolicies` for whatever the hooks read/write ([Rewrite B]). Worked example: `03-infrastructure.md`.

## Step 4 — frontend

The five client files live in `web/src/voice/`. Call `configureVoiceApi(adapter, '/voice')` once at startup, then wire `useWebRTC` + `startVoiceSession`/`endVoiceSession` into the simulation screen with the reconnect loop — see `docs/frontend/voice-client.md` ([Rewrite G]).

## End-to-end sequence (sanity reference)

1. UI calls `POST /voice/{id}/start` → `authorize` → `on_start` → returns `{runtime_session_id, ice_servers}`.
2. Browser builds a relay-only, non-trickle offer with its own `ice_servers`; waits for ICE gathering to complete.
3. `POST /voice/{id}/signal` → control plane `invoke_agent_runtime(runtimeSessionId=...)` → runtime fetches KVS TURN, negotiates, **builds the pipeline inside the connection callback**, returns a relay-only answer.
4. Media flows browser↔runtime over KVS TURN. Each finalized turn: STT → referee → patient LLM → TTS, then your transcript handler + the v1 game events over the data channel.
5. Drop / cold-start stall → UI retries with a **fresh** `runtime_session_id` (same session id); the context provider re-seeds history so the agent resumes mid-conversation.
6. `POST /voice/{id}/end` (optional body `{runtime_session_id}` → the router best-effort invokes the runtime's `action: "end"` teardown first) → `on_end` → your closing transition + authoritative transcript.

## Extension-point reference

| Hook | Signature | Registered via | Runs in |
|---|---|---|---|
| Context provider | `async (session_id) -> SessionContext` | `set_context_provider` | runtime, pipeline loop |
| Transcript handler | `async (session_id, TranscriptMessage) -> None` | `set_transcript_handler` | runtime, pipeline loop |
| Processor factory | `(ProcessorFactoryArgs) -> list` | `set_processor_factory` | runtime, pipeline build |
| Session start hook | `async (session_id, PipelineContext, transport, emit) -> None` | `set_session_start_hook` | runtime, pipeline loop |
| Session end hook | `async (session_id) -> None` | `set_session_end_hook` | runtime, pipeline loop |
| `authorize` | `async (Request, session_id) -> None` | `create_voice_router` | API host |
| `on_start` | `async (session_id) -> None` | `create_voice_router` | API host |
| `on_end` | `async (session_id) -> list[TranscriptMessage]` | `create_voice_router` | API host |
| `emit` (data channel) | wired automatically | — | runtime |

## Verifying your integration

Cheap gates before a deploy, in order:

```bash
python3 -m compileall runtime/voice_kit runtime/bridge api
python3 -m pytest runtime/tests api/tests

# The pipecat-free gate — must succeed in a venv where `import pipecat` FAILS:
python3 -m venv /tmp/cp && /tmp/cp/bin/pip install ./runtime
/tmp/cp/bin/python -c "import voice_kit; voice_kit.create_voice_router()"

docker build --platform linux/arm64 -f runtime/Dockerfile.voice -t bridge-voice .
docker run --rm -p 8080:8080 bridge-voice   # then: curl localhost:8080/ping
```

The real gate is that ARM64 build + `/ping` smoke, then the post-deploy checklist — see `04-deploy-runbook.md`.
