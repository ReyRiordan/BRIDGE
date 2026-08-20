# Configuration

All kit config is env-driven through `runtime/voice_kit/config.py` (pydantic-settings; **two** env files are read — one next to the package, then `.env` in the CWD (the repo root). Later wins, real env vars beat both, missing files are inert. The second entry exists because the package-adjacent path resolves into site-packages for the installed `api/` copy); BRIDGE's game-engine vars are plain `os.environ` reads in `runtime/bridge/config.py`. Programmatic override: `voice_kit.configure(**overrides)`. the repo-root `.env.example` is the copyable template.

**Consumer** column: which process needs the var — the AgentCore **runtime** container, the API **control-plane** host, or **both**.

## Core

| Env var | Consumer | Default | Notes |
|---|---|---|---|
| `ENV` | both | `development` | `production` makes the settings validator refuse `BRIDGE_LOCAL` |
| `BRIDGE_LOCAL` | both | `false` | Local dev mode: skips the KVS fetch + the relay-only SDP filter, implies `VOICE_INVOKER=local`, and **raises** on `ENV=production` or any AWS-backed provider. See [`../local-dev.md`](../local-dev.md) |
| `AWS_REGION` | both | `us-east-1` | Region for KVS, Polly, Transcribe, Bedrock, AgentCore client |
| `KVS_CHANNEL_NAME` | both | — | Set by infra on both sides (runtime + API both fetch ICE servers) |
| `VOICE_RUNTIME_ARN` | control-plane | — | `invoke_agent_runtime` target; set by infra on the API host |
| `VOICE_INVOKER` | control-plane | `agentcore` | Router→runtime backend: `agentcore` \| `local`. Implied by `BRIDGE_LOCAL`; an explicit value still wins |
| `VOICE_RUNTIME_URL` | control-plane | `http://localhost:8080` | `LocalInvoker` target (`{url}/invocations`); unused by `agentcore` |
| `RUNTIME_SESSION_ID_PREFIX` | control-plane | `voicekit-` | prefix + 32 hex must land in AgentCore's 33–256 char window |
| `SESSION_TIME_LIMIT_MINUTES` | runtime | `30` | Default `SessionContext.time_limit_seconds` — the app's conversation cap, informational only (the kit never enforces it). Keep the frontend timer aligned |
| `IDLE_TIMEOUT_SECS` | runtime | `180` | Default `PipelineWorker` self-termination after this much speech-free time — the abandoned-container backstop, independent of the app's time limit. Per session, `SessionContext.idle_timeout_seconds` overrides it; the idle timeout cancels the pipeline, so it must never land inside a live session (`05-gotchas.md`) |
| `SYSTEM_PROMPT` / `SYSTEM_PROMPT_PATH` | runtime | — | Only used by the *default* context provider; ignored once you register your own |

## LLM

| Env var | Consumer | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | runtime | `openrouter` | `openrouter` \| `bedrock` |
| `LLM_MODEL` | runtime | `anthropic/claude-haiku-4.5` | Provider-specific ID format (see below) |
| `LLM_REASONING` | runtime | `none` | `none`\|`minimal`\|`low`\|`medium`\|`high`\|`xhigh`\|`max` |
| `LLM_PROVIDERS` | runtime | — | OpenRouter routing priority, comma-separated. **Silently ignored on bedrock** |
| `OPENROUTER_API_KEY` | runtime | — | Required when `openrouter` |
| `OPENROUTER_BASE_URL` | runtime | `https://openrouter.ai/api/v1` | |
| `AWS_BEDROCK_BASE_URL` | runtime | — | Required when `bedrock`; US East: `https://bedrock-mantle.us-east-1.api.aws/v1` |
| `AWS_BEDROCK_SIGV4_SERVICE` | runtime | `bedrock-mantle` | SigV4 signing service string |

| Provider | Auth | Model ID format | IAM |
|---|---|---|---|
| OpenRouter | Bearer key | `anthropic/claude-haiku-4.5` | none |
| Bedrock | IAM execution role / SigV4 (no token) | `openai.gpt-oss-120b`, `us.anthropic.claude-haiku-4-5-20251001-v1:0` | `bedrock-mantle:CreateInference` |

The voice path always uses the chat-completions surface (`/chat/completions`). GPT-5-family models are served only on Bedrock's Responses API and are **not usable** with this kit's LLM path — pick a chat-capable model.

## STT

| Env var | Consumer | Default | Notes |
|---|---|---|---|
| `STT_PROVIDER` | runtime | `transcribe` | `transcribe` \| `together` |
| `STT_PREROLL_MS` | runtime | `300` | Pre-speech ring buffer (onset recovery); don't tune Silero instead |
| `TOGETHER_API_KEY` | runtime | — | Required when `together` |

- **transcribe** — Amazon Transcribe Streaming, `en-US`, keyless (task role, `transcribe:StartStreamTranscription`). Audio stays in your AWS account.
- **together** — Parakeet (`nvidia/parakeet-tdt-0.6b-v3`) over HTTPS, `en`. **Sends user speech off-AWS — treat as dev-only** if you have a data-residency requirement.

## TTS

| Env var | Consumer | Default | Notes |
|---|---|---|---|
| `TTS_PROVIDER` | runtime | `polly` | `polly` \| `inworld` — used by the *default* context provider; your own provider can choose per session via `VoiceConfig` |
| `TTS_VOICE` | runtime | `Ruth` | Polly voice ID, or Inworld voice (e.g. `Ashley`) |
| `TTS_MODEL` | runtime | — | Inworld only (e.g. `inworld-tts-1.5-mini`) |
| `INWORLD_API_KEY` | runtime | — | Required when `inworld` (raises at construction if unset) |

- **polly** — `StartSpeechSynthesisStream`, generative engine, PCM 24 kHz, keyless.
- **inworld** — streaming HTTP, LINEAR16 48 kHz, Basic-auth key.

## Game engine (BRIDGE only)

Read by `runtime/bridge/config.py` — plain environment reads, all runtime-side. Every path falls back to the repo's own copy, so tests and local runs need no environment at all.

| Env var | Default | Notes |
|---|---|---|
| `SCENARIO_PATH` | `resources/scenario_1.json` | Actions, point values, time limit, TTS voice. Deployed: `/app/resources/scenario_1.json` |
| `REFEREE_PROMPT_PATH` | `resources/referee.txt` | Referee system prompt. Deployed: `/app/resources/referee.txt` |
| `PATIENT_PROMPT_PATH` | `resources/patient.txt` | Patient persona template. Deployed: `/app/resources/patient.txt` |
| `PATIENT_CASE_PATH` | `resources/patient.json` | Patient case file. Deployed: `/app/resources/patient.json` |
| `REFEREE_PROVIDER` | `openrouter` | `openrouter` \| `bedrock`. OpenRouter is sent `require_parameters` so routing only picks backends that honour the strict `json_schema` |
| `REFEREE_MODEL` | `anthropic/claude-haiku-4.5` | Separate from `LLM_MODEL` — the referee and the patient are different calls |
| `REFEREE_EFFORT` | `none` | Same vocabulary as `LLM_REASONING` |
| `REFEREE_TIMEOUT_SECONDS` | `7.0` | The referee is on every turn's serial critical path; past this it fails open (scores the turn as no-detection) |
| `GAME_GRACE_SECONDS` | `45.0` | Window between `game_over` and the reaper cancelling the pipeline, so the client can render the debrief |

Both files are read once and cached (`lru_cache`) for the container's life, as utf-8 — the scenario's `desc` strings carry curly quotes.

## AWS static keys (local dev only)

| Env var | Consumer | Notes |
|---|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | local only | **Never set in a deployed runtime** — they shadow the task role for every boto3 client (gotcha #1). The wrappers use them only when both are set |

## Deployed secrets: the SSM mechanism

Deployed runtimes never receive plain-text secret values. Instead infra injects:

- `SECRETS_FROM_SSM` — comma-separated secret *names* (e.g. `OPENROUTER_API_KEY,INWORLD_API_KEY,TOGETHER_API_KEY,AWS_BEDROCK_BASE_URL`)
- `SECRETS_SSM_PREFIXES` — comma-separated SSM parameter path prefixes, most specific first

At cold start, `_export_ssm_secrets()` (runs before `Settings` loads) resolves each name via `ssm:GetParameter` (WithDecryption) under the first prefix that has it and exports it into `os.environ`. Already-set env vars always win; unresolved names stay unset and the owning feature degrades as with any missing secret. Locally (no `SECRETS_FROM_SSM`) it's a no-op and `.env` is used. The task role needs `ssm:GetParameter` on the prefixes (handled by `amplify/voice-runtime.ts`); with Amplify, set values via `npx ampx sandbox secret set NAME`.

## Local mode (`BRIDGE_LOCAL=1`)

One umbrella flag, validated in `VoiceKitSettings`. It raises at startup — not silently degrades — in two cases:

| Condition | Error |
|---|---|
| `ENV=production` | `BRIDGE_LOCAL=1 is refused when ENV=production …` |
| `STT_PROVIDER=transcribe`, `TTS_PROVIDER=polly`, `LLM_PROVIDER=bedrock`, `REFEREE_PROVIDER=bedrock` | `BRIDGE_LOCAL=1 forbids AWS-backed providers …` (each offender named, with its fix) |

`REFEREE_PROVIDER` is read from `os.environ` rather than settings: it belongs to `bridge.config`, and `voice_kit` must never import `bridge` (container-only). Both processes share the same environment, so the guard holds on either side.

Note that `configure(**overrides)` assigns with `setattr` and therefore bypasses this validator — a pre-existing gap, documented rather than fixed.

Full walkthrough: [`../local-dev.md`](../local-dev.md).
