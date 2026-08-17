# Architecture

Voice-to-voice conversation pipeline: browser WebRTC ↔ a pipecat pipeline hosted on AWS Bedrock AgentCore, with an API-side control plane for signaling.

## Topology

```
Browser (React)                        API host (FastAPI, e.g. Lambda)       AgentCore Runtime (VPC)
  ├─ POST /voice/{id}/start ─────────► authorize + on_start hooks
  │     ◄── {runtime_session_id,          mint runtime_session_id
  │          ice_servers}                 fetch browser ICE (KVS)
  ├─ build non-trickle SDP offer
  ├─ POST /voice/{id}/signal ────────► invoke_agent_runtime(runtimeSessionId) ──► @app.entrypoint
  │     ◄──────────────── SDP answer ◄──────────────────────────────────────────── (relay-only)
  │═════════ WebRTC media P2P over KVS managed TURN ══════════════════════════════════►│
  │◄════════ transcript JSON over data channel ═════════════════════════════════════════│ → transcript handler
  └─ POST /voice/{id}/end ───────────► authorize + on_end hooks → transcript
```

Why the proxy: the AgentCore data plane emits no CORS headers, so the browser can never call it directly. Signaling goes server-to-server (IAM/SigV4); only media flows browser↔runtime, over KVS-managed TURN, relay-only on both sides.

## The pipeline (inside the runtime)

```
transport.input() → VADProcessor (Silero) → STTProcessor → LLMProcessor
    → TTSProcessor → TranscriptSinkProcessor → transport.output()
```

| Stage | Component | File | Details |
|---|---|---|---|
| Transport | Pipecat `SmallWebRTCTransport` (+ `SmallWebRTCRequestHandler`) | `runtime/voice_kit/runtime.py` | The handler owns the aiortc peer connection + SDP negotiation; we pass KVS managed-TURN `IceServer`s and filter the answer SDP to relay-only candidates |
| VAD | `VADProcessor(SileroVADAnalyzer())` | `runtime/voice_kit/pipeline.py` | Standalone processor (pipecat 1.3.0); emits `VADUserStarted/StoppedSpeakingFrame` the STT stage gates on. Silero left at defaults — onset recovery is the STT pre-roll's job |
| STT | `AmazonTranscribeSTT` / `TogetherSTT` | `runtime/voice_kit/providers/stt.py` | Streaming; provider via `STT_PROVIDER`. `STTProcessor` keeps a byte-bounded pre-roll ring buffer (`STT_PREROLL_MS`) so the utterance onset survives Silero's confirmation window |
| LLM | `OpenRouterChat` / `BedrockChat` | `runtime/voice_kit/providers/llm.py` | Single non-streaming chat call per turn; provider via `LLM_PROVIDER`, model via `LLM_MODEL`. History roles are chat-native `user`/`assistant` |
| TTS | `InworldTTS` (48 kHz) / `PollyTTS` (24 kHz generative) | `runtime/voice_kit/providers/tts.py` | Sync streaming generators, run in a worker thread; emitted as `TTSAudioRawFrame`s. Voice via the session's `VoiceConfig` |
| Sink | `TranscriptSinkProcessor` | `runtime/voice_kit/processors.py` | Awaits the host's transcript handler (your persistence) and emits each turn as JSON over the data channel (live UI transcript) |

Each finalized turn travels the whole chain as a `TranscriptMessageFrame` (not consumed by the stage that produced it), so the sink — last in the chain — sees both the user and assistant messages of every turn, in order.

## Two ids, two jobs

- **`session_id`** — your domain pointer. The invoke payload carries only `{session_id, sdp, type}`; the runtime resolves everything else (system prompt, voice, prior history) through the host-registered **context provider** ("pointer + re-fetch"). Stable for the life of a conversation.
- **`runtime_session_id`** — the AgentCore container-affinity key, minted by `/start` (prefix + uuid4 hex, 33–256 chars required). All `/signal` calls pin to it; the frontend regenerates it on a cold-start ICE failure while `session_id` stays the same, so the agent resumes with full context (via `SessionContext.initial_history`).

## Extension points (where your app plugs in)

| Point | Where | Replaces |
|---|---|---|
| Context provider `(session_id) → SessionContext` | `voice_kit.set_context_provider` | The source app's DB re-fetch of persona/prompt/voice/history |
| Transcript handler `(session_id, message) → None` | `voice_kit.set_transcript_handler` | Server-side per-turn persistence |
| `authorize(request, session_id)` | `create_voice_router(...)` | Auth + ownership + state guards on all three endpoints |
| `on_start(session_id)` | `create_voice_router(...)` | Your "session started" state transition |
| `on_end(session_id) → transcript` | `create_voice_router(...)` | Your closing transition + authoritative transcript read |

## Process model (runtime container)

- `BedrockAgentCoreApp` exposes `/ping` + `/invocations` on :8080 under uvicorn `--workers 1` (peer-connection state is in-process).
- A single asyncio loop runs `run_forever` in a daemon thread; `handle_offer` is a sync shim that submits `_handle_offer` via `run_coroutine_threadsafe` and blocks (≤30 s) for the SDP answer. Pipelines keep running on that loop after the invoke returns.
- One pipeline per session, enforced by cancel-and-await in `_run_task`.
- The pipeline self-terminates after `SESSION_TIME_LIMIT_MINUTES` of no speech (`PipelineWorker(idle_timeout_secs=...)`); AgentCore `maxLifetime` (1 h) is the infra backstop.
