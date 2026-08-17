// ---------------------------------------------------------------------------
// Deploy-time constants for the BRIDGE backend.
//
// Everything here is plain data so it can be unit-tested without a CDK synth
// (see constants.test.ts). `backend.ts` is the only consumer.
// ---------------------------------------------------------------------------

/**
 * VPC AZs for the voice runtime.
 *
 * AgentCore supports only the PHYSICAL zones use1-az1/az2/az4, and the
 * letter -> physical mapping is RANDOMIZED PER ACCOUNT. Wrong letters fail
 * runtime creation with "subnets are in unsupported availability zones" and
 * roll the whole stack back.
 *
 * VERIFIED for account 893361712219 (profile `compass-test`) on 2026-08-16 via
 * `aws ec2 describe-availability-zones`:
 *   us-east-1a -> use1-az1 | us-east-1b -> use1-az2 | us-east-1c -> use1-az4
 * Re-verify (and update this comment) before deploying into any other account.
 */
export const AVAILABILITY_ZONES = ['us-east-1a', 'us-east-1b', 'us-east-1c'];

/**
 * Provider API keys resolved at runtime cold start from SSM Parameter Store
 * (never as plain-text env values). Set them with
 * `npx ampx sandbox secret set <NAME>` / the Amplify console per branch.
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
 * Provider choices are deliberate legacy parity (Together STT / OpenRouter LLM /
 * Inworld TTS), not a copy accident: they keep the rewrite's voice behaviour
 * identical to the prototype students already trained on. Together sends student
 * audio off-AWS, which is acceptable for a training sim carrying no PHI;
 * switching to Amazon Transcribe later is a pure env change.
 *
 * The REFEREE_* / SCENARIO_PATH vars are the contract consumed by the game
 * engine in [Rewrite D]; everything else about the game (time limit, point
 * values, actions) comes from the scenario JSON itself.
 */
export const VOICE_CONFIG: Record<string, string> = {
  ENV: 'production',

  // --- LLM: the patient agent IS the pipeline LLM (no separate var block).
  LLM_PROVIDER: 'openrouter',
  LLM_MODEL: 'anthropic/claude-haiku-4.5',
  LLM_REASONING: 'none',

  // --- STT
  STT_PROVIDER: 'together',

  // --- TTS
  TTS_PROVIDER: 'inworld',
  TTS_VOICE: 'Mark',
  TTS_MODEL: 'inworld-tts-1.5-mini',

  // --- Session
  SESSION_TIME_LIMIT_MINUTES: '30',

  // --- Game engine ([Rewrite D]). REFEREE_* replaces the legacy SYSTEM_AGENT_*
  // naming — "system agent" terminology is retired.
  REFEREE_MODEL: 'anthropic/claude-haiku-4.5',
  REFEREE_EFFORT: 'none',
  SCENARIO_PATH: '/app/resources/scenario_1.json',
};

/** Lambda bundling knobs for the control-plane API. */
export const API_LAMBDA = {
  memorySizeMb: 512,
  timeoutSeconds: 30,
  handler: 'api.main.handler',
};

/**
 * Browser origins allowed to call the API. FastAPI's CORSMiddleware owns CORS —
 * the Function URL is deliberately configured WITHOUT `cors`, since configuring
 * both duplicates the headers and browsers reject the response.
 *
 * The Amplify Hosting origin is only known after the app is created, so it is
 * added here and the backend redeployed once (chicken-and-egg; expected — see
 * docs/backend/deployment.md).
 */
export const ALLOWED_ORIGINS = ['http://localhost:5173'];

/**
 * Paths excluded from the API Lambda's bundling asset. Keeps the asset small so
 * its hash stays stable and unchanged deploys skip the rebuild.
 */
export const API_ASSET_EXCLUDE = [
  // Legacy app trees (removed at final teardown)
  'frontend/',
  'backend/',
  'scenes/',
  'visuals/',
  'app.py',
  'render.yaml',
  // New trees the Lambda does not need
  'web/',
  'docs/',
  'amplify/',
  'scripts/',
  'node_modules/',
  // VCS / tooling / build junk
  '.git/',
  '.github/',
  '.claude/',
  '**/__pycache__/',
  '**/*.pyc',
  '**/tests/',
  '.venv*/',
  '*.egg-info/',
  'dist/',
  'coverage/',
  '.env',
  '.DS_Store',
];
