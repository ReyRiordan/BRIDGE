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

- [ ] A **system prompt** (env `SYSTEM_PROMPT`, or a context provider that builds one per session) — [Rewrite E]
- [ ] **Auth** on the three endpoints (`authorize` hook and/or FastAPI `dependencies`) — the defaults are open. Currently a documented `TODO(auth)` no-op at `authorize` in `api/main.py` — the `/signal` path is deliberately open until auth lands
- [ ] A **session id** concept (any string the app can re-fetch by)
- [ ] A **transcript sink** for server-side transcripts (`set_transcript_handler`) — [Rewrite D]
- [ ] Session **state transitions** (`on_start` / `on_end`)
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
# runtime/bridge/voice_app.py  ([Rewrite D]; uvicorn target in Dockerfile.voice)
import asyncio
from voice_kit import SessionContext, VoiceConfig, TranscriptMessage
from voice_kit import set_context_provider, set_transcript_handler
from voice_kit.runtime import app  # noqa: F401  — uvicorn target

async def provide_context(session_id: str) -> SessionContext:
    # Pointer + re-fetch: rebuild everything from session_id. Sync I/O must be
    # wrapped — this runs on the WebRTC packet-pump loop.
    record = await asyncio.to_thread(my_fetch_session, session_id)
    return SessionContext(
        system_prompt=my_build_prompt(record),
        voice=VoiceConfig(provider="polly", voice="Ruth"),
        initial_history=[TranscriptMessage(**m) for m in record.transcript],  # resume support
    )

async def store_turn(session_id: str, message: TranscriptMessage) -> None:
    await asyncio.to_thread(my_append_turn, session_id, message)

set_context_provider(provide_context)
set_transcript_handler(store_turn)
```

`runtime/bridge/` is already COPYed into the image; the CMD is repointed at the wrapper in [Rewrite D]. Until then the container runs the bare kit app, whose default context provider builds everything from `SYSTEM_PROMPT` + `TTS_*` env vars with no server-side transcript.

## Step 3 — infra

`amplify/voice-runtime.ts` is in place; `backend.ts` calls `addVoiceRuntime(...)` with verified AZs, the config constants, SSM secret names/prefixes, and `extraRuntimePolicies` for whatever the hooks read/write ([Rewrite B]). Worked example: `03-infrastructure.md`.

## Step 4 — frontend

The five client files live in `web/src/voice/`. Call `configureVoiceApi(adapter, '/voice')` once at startup, then wire `useWebRTC` + `startVoiceSession`/`endVoiceSession` into the simulation screen with the reconnect loop — see `docs/frontend/voice-client.md` ([Rewrite G]).

## End-to-end sequence (sanity reference)

1. UI calls `POST /voice/{id}/start` → `authorize` → `on_start` → returns `{runtime_session_id, ice_servers}`.
2. Browser builds a relay-only, non-trickle offer with its own `ice_servers`; waits for ICE gathering to complete.
3. `POST /voice/{id}/signal` → control plane `invoke_agent_runtime(runtimeSessionId=...)` → runtime fetches KVS TURN, negotiates, **builds the pipeline inside the connection callback**, returns a relay-only answer.
4. Media flows browser↔runtime over KVS TURN. Each finalized turn: STT → LLM → TTS, then your transcript handler + a JSON push over the data channel.
5. Drop / cold-start stall → UI retries with a **fresh** `runtime_session_id` (same session id); the context provider re-seeds history so the agent resumes mid-conversation.
6. `POST /voice/{id}/end` (optional body `{runtime_session_id}` → the router best-effort invokes the runtime's `action: "end"` teardown first) → `on_end` → your closing transition + authoritative transcript.

## Extension-point reference

| Hook | Signature | Registered via | Runs in |
|---|---|---|---|
| Context provider | `async (session_id) -> SessionContext` | `set_context_provider` | runtime, pipeline loop |
| Transcript handler | `async (session_id, TranscriptMessage) -> None` | `set_transcript_handler` | runtime, pipeline loop |
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
