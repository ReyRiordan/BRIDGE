/**
 * Local mode is opt-in through VITE_BRIDGE_LOCAL and nothing else — the module
 * is re-imported per case because its exports are evaluated once at import.
 */

import { afterEach, expect, test, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

const loadConfig = async () => {
  vi.resetModules()
  return import('./config')
}

test('defaults to cloud mode: relay-only, not local', async () => {
  vi.stubEnv('VITE_BRIDGE_LOCAL', '')

  const config = await loadConfig()

  expect(config.BRIDGE_LOCAL).toBe(false)
  expect(config.RELAY_ONLY).toBe(true)
})

test('VITE_BRIDGE_LOCAL=1 turns local mode on and relay-only off', async () => {
  vi.stubEnv('VITE_BRIDGE_LOCAL', '1')

  const config = await loadConfig()

  expect(config.BRIDGE_LOCAL).toBe(true)
  expect(config.RELAY_ONLY).toBe(false)
})

test('the API base is same-origin — the Vite proxy owns the local path', async () => {
  const config = await loadConfig()

  expect(config.API_BASE_URL).toBe('')
})
