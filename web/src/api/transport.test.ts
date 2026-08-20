/**
 * The composed control-plane URL. The deployed base is an absolute Function
 * URL and local mode's is '', so a base applied twice is invisible locally and
 * fatal in the cloud — these tests are the only place that divergence is
 * caught before a deploy.
 */

import { beforeEach, expect, test, vi } from 'vitest'
import { createTransport } from './transport'
import { configureVoiceApi, startVoiceSession } from '../voice/voiceApi'

const API = 'https://abc123.lambda-url.us-east-1.on.aws'

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
})

test('the deployed base is applied exactly once', async () => {
  configureVoiceApi(createTransport(API))

  await startVoiceSession('sess-1')

  expect(fetchMock).toHaveBeenCalledWith(
    `${API}/voice/sess-1/start`,
    expect.objectContaining({ method: 'POST' }),
  )
})

test('local mode composes a same-origin path', async () => {
  configureVoiceApi(createTransport(''))

  await startVoiceSession('sess-1')

  expect(fetchMock).toHaveBeenCalledWith(
    '/voice/sess-1/start',
    expect.objectContaining({ method: 'POST' }),
  )
})

test('a bodyless post sends no body — the end request model is optional', async () => {
  configureVoiceApi(createTransport(API))

  await startVoiceSession('sess-1')

  expect(fetchMock.mock.calls[0][1]).not.toHaveProperty('body')
})

test('an error response surfaces the control plane detail', async () => {
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ detail: 'runtime unavailable' }), {
      status: 502,
    }),
  )
  configureVoiceApi(createTransport(API))

  await expect(startVoiceSession('sess-1')).rejects.toThrow(
    'runtime unavailable',
  )
})
