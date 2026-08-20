import { readFileSync } from 'node:fs'
import { describe, expect, test } from 'vitest'
import {
  ALLOWED_ORIGINS,
  API_IMAGE_EXCLUDE,
  API_LAMBDA,
  RUNTIME_NAME_MAX_LENGTH,
  SECRET_NAMES,
  VOICE_CONFIG,
  VOICE_IMAGE_EXCLUDE,
  voiceRuntimeName,
} from './constants'

// No CDK synth here (see voice-runtime.test.ts): these guard the plain data
// that backend.ts feeds into constructs, which is where the deploy-time
// mistakes actually live.

describe('SECRET_NAMES', () => {
  test('matches the three provider keys the runtime resolves from SSM', () => {
    // Only INWORLD_API_KEY is load-bearing today (Transcribe and Bedrock use the
    // execution role); the other two are kept for a rollback and local parity.
    expect([...SECRET_NAMES].sort()).toEqual([
      'INWORLD_API_KEY',
      'OPENROUTER_API_KEY',
      'TOGETHER_API_KEY',
    ])
  })

  test('joins into the comma-separated form SECRETS_FROM_SSM expects', () => {
    expect(SECRET_NAMES.join(',')).not.toContain(' ')
  })
})

describe('VOICE_CONFIG', () => {
  test('every value is a string (container env vars)', () => {
    for (const [key, value] of Object.entries(VOICE_CONFIG)) {
      expect(typeof value, `${key} must be a string`).toBe('string')
    }
  })

  test('carries no secrets and no static AWS credentials', () => {
    // Gotcha #1: static AWS keys shadow the runtime task role for every boto3
    // client in the container. Secrets arrive via SSM, never as env values.
    for (const key of Object.keys(VOICE_CONFIG)) {
      expect(key).not.toMatch(/^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)$/)
      expect(SECRET_NAMES).not.toContain(key)
    }
  })

  test('pins AWS STT + LLM, with TTS still on Inworld', () => {
    expect(VOICE_CONFIG.STT_PROVIDER).toBe('transcribe')
    expect(VOICE_CONFIG.LLM_PROVIDER).toBe('bedrock')
    expect(VOICE_CONFIG.TTS_PROVIDER).toBe('inworld')
  })

  test('gives Bedrock the endpoint it has no default for', () => {
    // Unset, BedrockChat requests the literal URL `None/chat/completions`. It is
    // a public regional endpoint, so it is plain env config, not an SSM secret.
    expect(VOICE_CONFIG.AWS_BEDROCK_BASE_URL).toMatch(
      /^https:\/\/bedrock-mantle\.[a-z0-9-]+\.api\.aws\/v1$/,
    )
    expect(SECRET_NAMES).not.toContain('AWS_BEDROCK_BASE_URL')
  })

  test('both agents run the same Bedrock model', () => {
    // One model to reason about, one catalog entry to confirm against the
    // deploy region before shipping.
    expect(VOICE_CONFIG.REFEREE_PROVIDER).toBe(VOICE_CONFIG.LLM_PROVIDER)
    expect(VOICE_CONFIG.REFEREE_MODEL).toBe(VOICE_CONFIG.LLM_MODEL)
    expect(VOICE_CONFIG.LLM_MODEL).toBe('openai.gpt-oss-120b')
    expect(VOICE_CONFIG.LLM_REASONING).toBe('medium')
  })

  test('the referee reasons less than the patient, because it is timed', () => {
    // Not an oversight that these differ. The referee is on the serial critical
    // path and fails open past REFEREE_TIMEOUT_SECONDS, and on this model
    // `medium` peaked at 7.15s against that 7s budget while scoring no better
    // than `low` (docs/backend/prompts.md). The patient has no such budget.
    expect(VOICE_CONFIG.REFEREE_REASONING).toBe('low')
    expect(VOICE_CONFIG.REFEREE_REASONING).not.toBe(VOICE_CONFIG.LLM_REASONING)
  })

  test('bedrock model IDs carry no OpenRouter vendor prefix', () => {
    // `anthropic/claude-haiku-4.5` is OpenRouter's format; bedrock-mantle takes
    // the bare id, and the slashed form 404s at request time, not deploy time.
    for (const key of ['LLM_MODEL', 'REFEREE_MODEL']) {
      expect(VOICE_CONFIG[key], `${key}`).not.toContain('/')
    }
  })

  test('exposes the game-engine contract', () => {
    // The referee trio mirrors the patient's LLM_PROVIDER/MODEL/REASONING.
    expect(VOICE_CONFIG.REFEREE_PROVIDER).toBeTruthy()
    expect(VOICE_CONFIG.REFEREE_MODEL).toBeTruthy()
    expect(VOICE_CONFIG.REFEREE_REASONING).toBeTruthy()
    // Must match the image layout: Dockerfile.voice COPYs resources/ to /app.
    expect(VOICE_CONFIG.SCENARIO_PATH).toBe('/app/resources/scenario_1.json')
    expect(VOICE_CONFIG.REFEREE_PROMPT_PATH).toBe('/app/resources/referee.txt')
    expect(VOICE_CONFIG.PATIENT_PROMPT_PATH).toBe('/app/resources/patient.txt')
    expect(VOICE_CONFIG.PATIENT_CASE_PATH).toBe('/app/resources/patient.json')
    // Legacy SYSTEM_AGENT_* naming is retired.
    expect(Object.keys(VOICE_CONFIG)).not.toContain('SYSTEM_AGENT_MODEL')
  })

  test('game-engine timings are positive numeric strings', () => {
    expect(VOICE_CONFIG.REFEREE_TIMEOUT_SECONDS).toMatch(/^\d+(\.\d+)?$/)
    expect(Number(VOICE_CONFIG.REFEREE_TIMEOUT_SECONDS)).toBeGreaterThan(0)
    expect(VOICE_CONFIG.GAME_GRACE_SECONDS).toMatch(/^\d+(\.\d+)?$/)
    expect(Number(VOICE_CONFIG.GAME_GRACE_SECONDS)).toBeGreaterThan(0)
  })

  test('session limit is a positive integer string', () => {
    expect(VOICE_CONFIG.SESSION_TIME_LIMIT_MINUTES).toMatch(/^\d+$/)
    expect(Number(VOICE_CONFIG.SESSION_TIME_LIMIT_MINUTES)).toBeGreaterThan(0)
  })

  test('idle timeout is set independently of the session limit', () => {
    expect(VOICE_CONFIG.IDLE_TIMEOUT_SECS).toBe('180')
  })

  test('VAD knobs are pinned at pipecat defaults', () => {
    // Pinned so each tune is an env-only CFN update, not an image rebuild.
    // These values equal pipecat 1.3.0's own, so pinning changed no behaviour.
    expect(VOICE_CONFIG.VAD_CONFIDENCE).toBe('0.7')
    expect(VOICE_CONFIG.VAD_START_SECS).toBe('0.2')
    expect(VOICE_CONFIG.VAD_STOP_SECS).toBe('0.2')
    expect(VOICE_CONFIG.VAD_MIN_VOLUME).toBe('0.6')
    expect(VOICE_CONFIG.VAD_SPEECH_ACTIVITY_PERIOD).toBe('0.2')
    expect(VOICE_CONFIG.VAD_AUDIO_IDLE_TIMEOUT).toBe('1.0')
  })

  test('VAD probabilities stay inside the 0..1 range Silero accepts', () => {
    for (const key of ['VAD_CONFIDENCE', 'VAD_MIN_VOLUME']) {
      const value = Number(VOICE_CONFIG[key])
      expect(value).toBeGreaterThanOrEqual(0)
      expect(value).toBeLessThanOrEqual(1)
    }
  })

  test('VAD sample rate is left pipeline-negotiated', () => {
    // Silero accepts only 8000/16000 and STTProcessor's pre-roll math assumes
    // 16 kHz, so this one is deliberately not a knob.
    expect(Object.keys(VOICE_CONFIG)).not.toContain('VAD_SAMPLE_RATE')
  })

  test('carries no local-dev flag, and pins ENV=production as the backstop', () => {
    // Two independent locks on local mode. Absent flag: the container never
    // starts with the KVS fetch and the relay-only SDP filter switched off.
    // ENV=production: even a leaked BRIDGE_LOCAL makes VoiceKitSettings raise
    // at container start rather than silently degrade the deployed ICE path.
    expect(Object.keys(VOICE_CONFIG)).not.toContain('BRIDGE_LOCAL')
    expect(Object.keys(VOICE_CONFIG)).not.toContain('VOICE_INVOKER')
    expect(VOICE_CONFIG.ENV).toBe('production')
  })
})

describe('voiceRuntimeName', () => {
  const SANDBOX = { namespace: 'bridge', name: 'reyriordan', type: 'sandbox' }
  const BRANCH = { namespace: 'd8vcc5ya6qjw1', name: 'main', type: 'branch' }

  test('sandbox and branch backends get distinct names', () => {
    // AgentCore names are unique per ACCOUNT; both stacks coexist by design, so
    // a shared name fails the second deploy with AlreadyExists.
    expect(voiceRuntimeName(SANDBOX)).not.toBe(voiceRuntimeName(BRANCH))
  })

  test('two apps sharing a branch name still differ', () => {
    expect(voiceRuntimeName({ namespace: 'appA', name: 'main', type: 'branch' })).not.toBe(
      voiceRuntimeName({ namespace: 'appB', name: 'main', type: 'branch' }),
    )
  })

  test.each([
    SANDBOX,
    BRANCH,
    { namespace: 'd8vcc5ya6qjw1', name: 'feature/some-very-long-branch-name-here', type: 'branch' },
    { namespace: 'a'.repeat(60), name: 'b'.repeat(60), type: 'sandbox' },
  ])('satisfies the AgentCore name pattern: %o', (id) => {
    const name = voiceRuntimeName(id)
    expect(name).toMatch(/^[a-zA-Z][a-zA-Z0-9_]{0,47}$/)
    expect(name.length).toBeLessThanOrEqual(RUNTIME_NAME_MAX_LENGTH)
  })

  test('over-long identities stay distinct after truncation', () => {
    const a = voiceRuntimeName({ namespace: 'x'.repeat(40), name: 'release-alpha', type: 'branch' })
    const b = voiceRuntimeName({ namespace: 'x'.repeat(40), name: 'release-beta', type: 'branch' })
    expect(a).not.toBe(b)
  })

  test('is deterministic', () => {
    expect(voiceRuntimeName(BRANCH)).toBe(voiceRuntimeName(BRANCH))
  })
})

describe('API_LAMBDA', () => {
  test('fits inside the Function URL 30s response window', () => {
    expect(API_LAMBDA.timeoutSeconds).toBeLessThanOrEqual(30)
  })
})

describe('ALLOWED_ORIGINS', () => {
  test('includes the Vite dev server and the Hosting origin', () => {
    expect(ALLOWED_ORIGINS).toContain('http://localhost:5173')
    expect(ALLOWED_ORIGINS).toContain('https://main.d8vcc5ya6qjw1.amplifyapp.com')
  })

  test('is never a wildcard — the Function URL sets no CORS of its own', () => {
    expect(ALLOWED_ORIGINS).not.toContain('*')
  })

  test('every entry is a scheme-qualified origin with no trailing slash', () => {
    // A trailing slash or bare host silently fails to match the browser's
    // Origin header, which surfaces only as a CORS error in the SPA.
    for (const o of ALLOWED_ORIGINS) {
      expect(o).toMatch(/^https?:\/\//)
      expect(o).not.toMatch(/\/$/)
    }
  })
})

describe('image asset excludes', () => {
  // These shape the asset HASHES. Both images build from the repo root, so each
  // must subtract the other's tree — and neither may subtract a tree its own
  // Dockerfile COPYs, which would freeze the hash and ship stale code silently.
  test('API_IMAGE_EXCLUDE drops the container-only runtime trees', () => {
    expect(API_IMAGE_EXCLUDE).toContain('runtime/bridge/')
  })

  test('API_IMAGE_EXCLUDE keeps everything api/Dockerfile.api COPYs', () => {
    for (const path of ['api/', 'resources/', 'runtime/voice_kit/', 'runtime/pyproject.toml']) {
      expect(API_IMAGE_EXCLUDE).not.toContain(path)
    }
  })

  test('VOICE_IMAGE_EXCLUDE drops api/, which the voice image never COPYs', () => {
    expect(VOICE_IMAGE_EXCLUDE).toContain('api/')
  })
})

describe('.dockerignore', () => {
  const lines = readFileSync(new URL('../.dockerignore', import.meta.url), 'utf8')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))

  test('does not ignore api/ — the API image COPYs it', () => {
    // CDK merges .dockerignore with the per-asset exclude and hashes the STAGED
    // copy, so ignoring api/ here would freeze the API asset's hash and deploy
    // stale code with no error. api/ is subtracted from the VOICE asset only,
    // via VOICE_IMAGE_EXCLUDE.
    expect(lines).not.toContain('api/')
  })

  test('ignores the CDK output dirs that live inside the context root', () => {
    // The context is the repo root and CDK stages into .amplify/artifacts/
    // cdk.out/ — without these, staging copies its own output into itself until
    // the path hits ENAMETOOLONG. (`amplify/` does NOT match `.amplify/`.)
    expect(lines).toContain('.amplify/')
    expect(lines).toContain('cdk.out/')
  })

  test('ignores local build junk recursively, at any depth', () => {
    // `pip install ./runtime` regenerates runtime/build/ + runtime/*.egg-info;
    // root-anchored patterns would let that untracked junk bust both hashes.
    for (const pattern of ['**/build/', '**/dist/', '**/*.egg-info/']) {
      expect(lines).toContain(pattern)
    }
  })
})
