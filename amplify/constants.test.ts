import { describe, expect, test } from 'vitest'
import {
  ALLOWED_ORIGINS,
  API_ASSET_EXCLUDE,
  API_LAMBDA,
  AVAILABILITY_ZONES,
  RUNTIME_NAME_MAX_LENGTH,
  SECRET_NAMES,
  VOICE_CONFIG,
  voiceRuntimeName,
} from './constants'

// No CDK synth here (see voice-runtime.test.ts): these guard the plain data
// that backend.ts feeds into constructs, which is where the deploy-time
// mistakes actually live.

describe('AVAILABILITY_ZONES', () => {
  // AgentCore supports only physical use1-az1/az2/az4; these letters are the
  // verified mapping for account 893361712219. Anything else rolls the stack
  // back at deploy time.
  test('is exactly the three verified us-east-1 letters', () => {
    expect(AVAILABILITY_ZONES).toEqual(['us-east-1a', 'us-east-1b', 'us-east-1c'])
  })
})

describe('SECRET_NAMES', () => {
  test('matches the three provider keys the runtime resolves from SSM', () => {
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

  test('pins the legacy-parity provider trio', () => {
    expect(VOICE_CONFIG.STT_PROVIDER).toBe('together')
    expect(VOICE_CONFIG.LLM_PROVIDER).toBe('openrouter')
    expect(VOICE_CONFIG.TTS_PROVIDER).toBe('inworld')
  })

  test('exposes the game-engine contract', () => {
    expect(VOICE_CONFIG.REFEREE_MODEL).toBeTruthy()
    expect(VOICE_CONFIG.REFEREE_EFFORT).toBeTruthy()
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
  test('handler points at the bundled api/main.py', () => {
    expect(API_LAMBDA.handler).toBe('api.main.handler')
  })

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

describe('API_ASSET_EXCLUDE', () => {
  test('drops the legacy trees and node_modules from the Lambda asset', () => {
    for (const path of ['frontend/', 'backend/', 'visuals/', 'web/', 'node_modules/']) {
      expect(API_ASSET_EXCLUDE).toContain(path)
    }
  })

  test('excludes CDK output dirs, which live inside the asset source', () => {
    // The asset source is the repo root and CDK stages into
    // .amplify/artifacts/cdk.out/ — without these, staging copies its own
    // output into itself until the path hits ENAMETOOLONG.
    expect(API_ASSET_EXCLUDE).toContain('.amplify/')
    expect(API_ASSET_EXCLUDE).toContain('cdk.out/')
  })

  test('keeps api/ and resources/ — the bundling step copies both', () => {
    expect(API_ASSET_EXCLUDE).not.toContain('api/')
    expect(API_ASSET_EXCLUDE).not.toContain('resources/')
  })
})
