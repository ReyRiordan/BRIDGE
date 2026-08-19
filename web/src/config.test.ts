/**
 * Local mode is opt-in through VITE_BRIDGE_LOCAL and nothing else — the module
 * is re-imported per case because its exports are evaluated once at import.
 */

import { afterEach, expect, test, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
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

test('local mode keeps the API base same-origin — the Vite proxy owns it', async () => {
  vi.stubEnv('VITE_BRIDGE_LOCAL', '1')
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)

  const config = await loadConfig()

  await expect(config.resolveApiBaseUrl()).resolves.toBe('')
  expect(config.getApiBaseUrl()).toBe('')
  // No outputs file exists locally, and fetching one would be an AWS call.
  expect(fetchMock).not.toHaveBeenCalled()
})

test('deployed mode resolves custom.apiUrl and trims its trailing slash', async () => {
  vi.stubEnv('VITE_BRIDGE_LOCAL', '')
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ custom: { apiUrl: 'https://api.example.com/' } }),
    })),
  )

  const config = await loadConfig()

  await expect(config.resolveApiBaseUrl()).resolves.toBe(
    'https://api.example.com',
  )
  expect(config.getApiBaseUrl()).toBe('https://api.example.com')
})

test('an unreachable outputs file rejects — the bootstrap shows a load error', async () => {
  vi.stubEnv('VITE_BRIDGE_LOCAL', '')
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: false, status: 404 })),
  )

  const config = await loadConfig()

  await expect(config.resolveApiBaseUrl()).rejects.toThrow(/404/)
})

test('outputs without custom.apiUrl reject rather than resolving empty', async () => {
  vi.stubEnv('VITE_BRIDGE_LOCAL', '')
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => ({ custom: {} }) })),
  )

  const config = await loadConfig()

  await expect(config.resolveApiBaseUrl()).rejects.toThrow(/custom.apiUrl/)
})
