import { readFileSync } from 'node:fs'
import { expect, test } from 'vitest'
import { addVoiceRuntime } from './voice-runtime'

// No CDK synth here: the construct resolves account/region context and builds a
// docker image asset, neither of which exists in CI. This guards the module's
// shape — that `backend.ts` has something importable to call.
test('exports addVoiceRuntime', () => {
  expect(typeof addVoiceRuntime).toBe('function')
})

// Local dev mode must be un-deployable. This is the first of two independent
// locks: the container env carries neither flag, so the runtime can never be
// started in local mode from infra. (The second lock lives in
// VoiceKitSettings, which refuses BRIDGE_LOCAL when ENV=production.)
test('never injects the local-dev flags into the runtime container', () => {
  const source = readFileSync(new URL('./voice-runtime.ts', import.meta.url), 'utf8')

  expect(source).not.toContain('BRIDGE_LOCAL')
  expect(source).not.toContain('VOICE_INVOKER')
})
