import { expect, test } from 'vitest'
import { addVoiceRuntime } from './voice-runtime'

// No CDK synth here: the construct resolves account/region context and builds a
// docker image asset, neither of which exists in CI. This guards the module's
// shape — that `backend.ts` ([Rewrite B]) has something importable to call.
test('exports addVoiceRuntime', () => {
  expect(typeof addVoiceRuntime).toBe('function')
})
