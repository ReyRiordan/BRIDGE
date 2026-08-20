// ---------------------------------------------------------------------------
// Deploy-time constants for the BRIDGE backend.
//
// Everything here is plain data so it can be unit-tested without a CDK synth
// (see constants.test.ts). `backend.ts` is the only consumer.
// ---------------------------------------------------------------------------

/**
 * Provider API keys resolved at runtime cold start from SSM Parameter Store
 * (never as plain-text env values). Set them with
 * `npx ampx sandbox secret set <NAME>` / the Amplify console per branch.
 *
 * Only INWORLD_API_KEY is load-bearing now that STT and both LLMs are on AWS
 * (Transcribe and Bedrock authenticate with the execution role). The OpenRouter
 * and Together keys stay listed so a rollback to those providers is an env
 * change with no secret round-trip, and so local dev keeps the same names.
 */
export const SECRET_NAMES = [
  'OPENROUTER_API_KEY',
  'TOGETHER_API_KEY',
  'INWORLD_API_KEY',
];

/**
 * Single source of truth for the voice runtime's container env. All values must
 * be strings — they become container environment variables verbatim.
 *
 * STT and both LLM agents run on AWS. Amazon Transcribe keeps student audio
 * inside the account, and Bedrock serves the patient and the referee off the
 * execution role, so neither needs an API key. TTS stays on Inworld.
 *
 * Moving the referee to Bedrock costs it structured output: the bedrock-mantle
 * chat-completions surface ignores `response_format`, so the JSON shape is
 * enforced by `resources/referee.txt` alone. `runtime/bridge/referee.py` parses
 * defensively and fails open, and `runtime/evals/referee_eval.py --provider
 * bedrock` measures the parse rate rather than assuming it.
 *
 * The REFEREE_* / SCENARIO_PATH / GAME_* vars are the game engine's contract
 * (`runtime/bridge/config.py`); everything else about the game (time limit,
 * point values, actions) comes from the scenario JSON itself.
 */
export const VOICE_CONFIG: Record<string, string> = {
  ENV: 'production',

  // --- LLM: the patient agent is the kit's pipeline LLM, so it has no var
  // block of its own. The REFEREE_* trio below mirrors these three names.
  LLM_PROVIDER: 'bedrock',
  LLM_MODEL: 'openai.gpt-oss-120b',
  LLM_REASONING: 'medium',
  // Public regional endpoint, not a secret — a plain env var rather than an SSM
  // entry. Unset, BedrockChat builds the request URL `None/chat/completions`.
  // The region must match the deploy region.
  AWS_BEDROCK_BASE_URL: 'https://bedrock-mantle.us-east-1.api.aws/v1',

  // --- STT. `transcribe` is also the kit default; pinned because this file is
  // the source of truth for every deployed value.
  STT_PROVIDER: 'transcribe',

  // --- VAD. These pin pipecat's own defaults, so they are a tuning surface,
  // not a behaviour change. Tune them one at a time — see voice-kit gotcha #19
  // before lowering VAD_CONFIDENCE or VAD_START_SECS.
  VAD_CONFIDENCE: '0.7',
  VAD_START_SECS: '0.2',
  VAD_STOP_SECS: '0.2',
  VAD_MIN_VOLUME: '0.6',
  VAD_SPEECH_ACTIVITY_PERIOD: '0.2',
  VAD_AUDIO_IDLE_TIMEOUT: '1.0',

  // --- TTS
  TTS_PROVIDER: 'inworld',
  TTS_VOICE: 'Mark',
  TTS_MODEL: 'inworld-tts-1.5-mini',

  // --- Session. SESSION_TIME_LIMIT_MINUTES is the app's conversation cap
  // (informational to the kit); IDLE_TIMEOUT_SECS is the pipeline's
  // self-termination backstop for abandoned containers — independent knobs.
  SESSION_TIME_LIMIT_MINUTES: '30',
  IDLE_TIMEOUT_SECS: '180',

  // --- Game engine. REFEREE_* replaces the legacy SYSTEM_AGENT_* naming —
  // "system agent" terminology is retired. The paths are the image's copies
  // (Dockerfile.voice COPYs resources/ to /app/resources).
  // The referee's own LLM, symmetric with the patient's LLM_* trio above.
  // `low`, not the patient's `medium`: the referee is measured, and on this
  // model medium was both slower (7.15s max, against the 7s budget below) and
  // no more accurate. See docs/backend/prompts.md for the numbers.
  REFEREE_PROVIDER: 'bedrock',
  REFEREE_MODEL: 'openai.gpt-oss-120b',
  REFEREE_REASONING: 'low',
  // The referee is on the serial critical path of every turn, so it fails open
  // rather than making the student wait.
  REFEREE_TIMEOUT_SECONDS: '7',
  SCENARIO_PATH: '/app/resources/scenario_1.json',
  REFEREE_PROMPT_PATH: '/app/resources/referee.txt',
  PATIENT_PROMPT_PATH: '/app/resources/patient.txt',
  PATIENT_CASE_PATH: '/app/resources/patient.json',
  // Grace window between `game_over` and tearing the pipeline down, so the
  // client can render the debrief.
  GAME_GRACE_SECONDS: '45',
};

/**
 * AgentCore runtime names are unique PER ACCOUNT, so the shared default
 * ('VoiceRuntime') makes the second backend fail with `AlreadyExists` — the
 * sandbox and branch stacks coexist by design. Derive one per backend, the same
 * reasoning as the KVS channel name.
 *
 * The API accepts `[a-zA-Z][a-zA-Z0-9_]{0,47}`: letters, digits and
 * underscores only, so the stack name's hyphens cannot be used raw.
 */
export const RUNTIME_NAME_MAX_LENGTH = 48;

export function voiceRuntimeName(backendId: {
  namespace: string;
  name: string;
  type: string;
}): string {
  const clean = (s: string) => (s ?? '').replace(/[^a-zA-Z0-9]/g, '');
  const full = `Voice_${clean(backendId.namespace)}_${clean(backendId.name)}_${clean(backendId.type)}`;
  if (full.length <= RUNTIME_NAME_MAX_LENGTH) return full;
  // Truncation alone could collide (two long branch names sharing a prefix), so
  // keep a deterministic digest of the full identity on the end.
  let hash = 5381;
  for (let i = 0; i < full.length; i++) hash = ((hash * 33) ^ full.charCodeAt(i)) >>> 0;
  const suffix = `_${hash.toString(36)}`;
  return full.slice(0, RUNTIME_NAME_MAX_LENGTH - suffix.length) + suffix;
}

/**
 * Sizing knobs for the control-plane API Lambda. No `handler`: it is a
 * container-image function (api/Dockerfile.api), so the entry point is the
 * image's CMD (uvicorn under the Lambda Web Adapter), not a handler string.
 */
export const API_LAMBDA = {
  memorySizeMb: 512,
  timeoutSeconds: 30,
};

/**
 * Browser origins allowed to call the API. FastAPI's CORSMiddleware owns CORS —
 * the Function URL is deliberately configured WITHOUT `cors`, since configuring
 * both duplicates the headers and browsers reject the response.
 *
 * Amplify Hosting origins are deterministic —
 * `https://<branch>.<appId>.amplifyapp.com` — so the branch origin can be added
 * as soon as the app id is known, rather than waiting for the first build and
 * redeploying the backend afterwards.
 */
export const ALLOWED_ORIGINS = [
  'http://localhost:5173', // Vite dev server
  'https://main.d8vcc5ya6qjw1.amplifyapp.com', // Amplify Hosting, `main` branch
];

/**
 * Per-asset excludes for the two container images, layered ON TOP of the root
 * `.dockerignore` (CDK merges the two).
 *
 * These exist for HASH STABILITY, not size. Both images build from the repo
 * root, and CDK derives each asset's hash from its staged copy — so without
 * each list subtracting the other image's tree, an `api/` edit would rebuild
 * and re-push the pipecat image and restart the AgentCore runtime (and vice
 * versa).
 *
 * The inverse rule matters more: never exclude a tree the image COPYs. The
 * hash then freezes and the deploy ships stale code with no error. `api/`,
 * `resources/`, `runtime/voice_kit/` and `runtime/pyproject.toml` are all
 * COPYed by api/Dockerfile.api and must stay out of API_IMAGE_EXCLUDE.
 */
export const API_IMAGE_EXCLUDE = [
  'runtime/bridge/',
  'runtime/evals/',
  'runtime/requirements-voice.txt',
  'runtime/Dockerfile.voice',
];

export const VOICE_IMAGE_EXCLUDE = ['api/'];
