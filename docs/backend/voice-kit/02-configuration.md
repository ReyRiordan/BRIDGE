# Configuration

All config is env-driven through `runtime/voice_kit/config.py` (pydantic-settings; a `.env` next to the package works locally). Programmatic override: `voice_kit.configure(**overrides)`. the repo-root `.env.example` is the copyable template.

**Consumer** column: which process needs the var — the AgentCore **runtime** container, the API **control-plane** host, or **both**.

## Core

| Env var | Consumer | Default | Notes |
|---|---|---|---|
| `ENV` | both | `development` | |
| `AWS_REGION` | both | `us-east-1` | Region for KVS, Polly, Transcribe, Bedrock, AgentCore client |
| `KVS_CHANNEL_NAME` | both | — | Set by infra on both sides (runtime + API both fetch ICE servers) |
| `VOICE_RUNTIME_ARN` | control-plane | — | `invoke_agent_runtime` target; set by infra on the API host |
| `VOICE_INVOKER` | control-plane | `agentcore` | Router→runtime backend: `agentcore` \| `local` (`local` lands in [Rewrite H]) |
| `RUNTIME_SESSION_ID_PREFIX` | control-plane | `voicekit-` | prefix + 32 hex must land in AgentCore's 33–256 char window |
| `SESSION_TIME_LIMIT_MINUTES` | runtime | `30` | Pipeline idle-timeout self-termination; keep the frontend timer aligned |
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

## AWS static keys (local dev only)

| Env var | Consumer | Notes |
|---|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | local only | **Never set in a deployed runtime** — they shadow the task role for every boto3 client (gotcha #1). The wrappers use them only when both are set |

## Deployed secrets: the SSM mechanism

Deployed runtimes never receive plain-text secret values. Instead infra injects:

- `SECRETS_FROM_SSM` — comma-separated secret *names* (e.g. `OPENROUTER_API_KEY,INWORLD_API_KEY,TOGETHER_API_KEY,AWS_BEDROCK_BASE_URL`)
- `SECRETS_SSM_PREFIXES` — comma-separated SSM parameter path prefixes, most specific first

At cold start, `_export_ssm_secrets()` (runs before `Settings` loads) resolves each name via `ssm:GetParameter` (WithDecryption) under the first prefix that has it and exports it into `os.environ`. Already-set env vars always win; unresolved names stay unset and the owning feature degrades as with any missing secret. Locally (no `SECRETS_FROM_SSM`) it's a no-op and `.env` is used. The task role needs `ssm:GetParameter` on the prefixes (handled by `amplify/voice-runtime.ts`); with Amplify, set values via `npx ampx sandbox secret set NAME`.
