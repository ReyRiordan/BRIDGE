# Integration guide

How to wire the kit into an Amplify Gen 2 project with a FastAPI backend. Read `00-architecture.md` first for the shape; `05-gotchas.md` before your first deploy.

## What you must supply

- [ ] A **system prompt** (env `SYSTEM_PROMPT`, or a context provider that builds one per session)
- [ ] **Auth** on the three endpoints (`authorize` hook and/or FastAPI `dependencies`) — the defaults are open
- [ ] A **session id** concept (any string your app can re-fetch by)
- [ ] A **transcript sink** if you want server-side transcripts (`set_transcript_handler`)
- [ ] Session **state transitions** if your domain has them (`on_start` / `on_end`)
- [ ] Verified **AgentCore AZ letters** for your account (gotcha #4)
- [ ] Secrets in **SSM** for whichever providers you enable

## Step 1 — copy the backend package

Copy `backend/voice_kit/`, `backend/Dockerfile.voice`, and `backend/requirements-voice.txt` into your backend docker context (e.g. `amplify/backend/`). The package is imported as plain `voice_kit.*` — put it wherever your `PYTHONPATH` resolves.

## Step 2 — control plane (API host)

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
    # dependencies=[Depends(get_current_user)],        # FastAPI-native auth also works
    prefix="/voice",
))
register_exception_handlers(app)                       # 502s with CORS headers (gotcha #22)
```

Env needed here: `VOICE_RUNTIME_ARN`, `KVS_CHANNEL_NAME`, `AWS_REGION` (infra injects the first two).

## Step 3 — runtime (AgentCore container)

Write a tiny wrapper module that registers your hooks and re-exports the app:

```python
# my_voice_app.py  (in the docker context, next to voice_kit/)
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

Then in `Dockerfile.voice`: add `COPY my_voice_app.py /app/` and switch the CMD to `uvicorn my_voice_app:app ...` (a commented line is already there).

Skipping this step entirely is valid for a single-persona agent: the default context provider builds everything from `SYSTEM_PROMPT` + `TTS_*` env vars, with no server-side transcript.

## Step 4 — infra

Follow `infra/README.md`: copy `infra/voice-runtime.ts` into `amplify/`, call `addVoiceRuntime(...)` from your `backend.ts` with verified AZs, your config constants, SSM secret names/prefixes, and `extraRuntimePolicies` for whatever your hooks read/write.

## Step 5 — frontend

Follow `frontend/USAGE.md`: copy the five files, `configureVoiceApi(yourAxiosAdapter, '/voice')`, then wire `useWebRTC` + `startVoiceSession`/`endVoiceSession` into your UI with the reconnect loop.

## End-to-end sequence (sanity reference)

1. UI calls `POST /voice/{id}/start` → `authorize` → `on_start` → returns `{runtime_session_id, ice_servers}`.
2. Browser builds a relay-only, non-trickle offer with its own `ice_servers`; waits for ICE gathering to complete.
3. `POST /voice/{id}/signal` → control plane `invoke_agent_runtime(runtimeSessionId=...)` → runtime fetches KVS TURN, negotiates, **builds the pipeline inside the connection callback**, returns a relay-only answer.
4. Media flows browser↔runtime over KVS TURN. Each finalized turn: STT → LLM → TTS, then your transcript handler + a JSON push over the data channel.
5. Drop / cold-start stall → UI retries with a **fresh** `runtime_session_id` (same session id); the context provider re-seeds history so the agent resumes mid-conversation.
6. `POST /voice/{id}/end` → `on_end` → your closing transition + authoritative transcript.

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

Cheap gates before a deploy: `python3 -m compileall backend/voice_kit`, then in your API venv `python -c "import voice_kit; voice_kit.create_voice_router()"`. The real gate is the ARM64 docker build + `/ping` smoke and the post-deploy checklist — see `04-deploy-runbook.md`.
