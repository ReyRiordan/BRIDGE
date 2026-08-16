# voice-pipeline-kit

A self-contained, drag-and-drop voice-to-voice pipeline:

**browser WebRTC → AWS Bedrock AgentCore → pipecat (STT → LLM → TTS)**

- **STT**: Amazon Transcribe Streaming (default) or Together AI (Parakeet)
- **LLM**: OpenRouter or AWS Bedrock (bedrock-mantle, SigV4) — env-switchable
- **TTS**: Amazon Polly (generative streaming, default) or Inworld
- **Transport**: WebRTC over KVS-managed TURN (relay-only), signaling proxied through your API
- **Hosting**: AgentCore Runtime (ARM64 container), infra as a copyable Amplify Gen 2 / CDK module

Extracted from a production deployment; the code paths, IAM grants, and ~28 documented gotchas are battle-tested. Domain logic has been stripped and replaced with clean extension points (a per-session context provider, a transcript sink, and auth/lifecycle hooks on the signaling router).

## Layout

```
backend/
  voice_kit/            Python package: runtime (pipecat on AgentCore), providers,
                        control-plane FastAPI router, config, extension points
  Dockerfile.voice      ARM64 runtime image
  requirements-voice.txt
  .env.example
frontend/               WebRTC service + React hook + API client + wiring notes (USAGE.md)
infra/                  addVoiceRuntime() CDK module for Amplify Gen 2 backend.ts + README
docs/
  00-architecture.md    Topology, pipeline chain, the two session ids, extension points
  01-integration-guide.md   Step-by-step wiring (start here)
  02-configuration.md   Every env var, provider tables, SSM secrets mechanism
  03-infrastructure.md  VPC / KVS / AgentCore / IAM, with the reason for every grant
  04-deploy-runbook.md  First-deploy steps, post-deploy checklist, symptom→cause table
  05-gotchas.md         The hard-won lessons — read before touching anything
```

## Quick start

1. Read `docs/01-integration-guide.md`.
2. Copy `backend/voice_kit` + `Dockerfile.voice` + `requirements-voice.txt` into your backend.
3. Mount the signaling router in your FastAPI app with your auth/lifecycle hooks.
4. (Optional) Write a runtime wrapper registering a context provider + transcript sink.
5. Copy `infra/voice-runtime.ts` into `amplify/`, call `addVoiceRuntime(...)` from `backend.ts`.
6. Copy the `frontend/` files and wire them per `frontend/USAGE.md`.
7. Deploy per `docs/04-deploy-runbook.md`.

## Notes

- This folder ships its own `.gitignore` (`*`) so it stays untracked in the source repo. Delete that file after dropping the folder into your target repo if you want it committed there.
- pipecat-ai was verified at **1.3.0** and is deliberately loosely pinned — pin it for reproducibility (see `docs/05-gotchas.md` #26).
- No tests or runnable demo ship with the kit; verification is the import gates + the docker `/ping` smoke + the post-deploy checklist (runbook).
