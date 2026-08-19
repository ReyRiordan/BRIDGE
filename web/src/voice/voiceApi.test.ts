/**
 * The transport seam: what each endpoint function actually posts. The `/end`
 * body matters most — without `runtime_session_id` the runtime's pipeline
 * lingers until the idle timeout.
 */

import { beforeEach, expect, test, vi } from 'vitest'
import {
  configureVoiceApi,
  endVoiceSession,
  startVoiceSession,
  type VoiceApiTransport,
} from './voiceApi'

let post: ReturnType<typeof vi.fn>
/** The kit's transport is generic; the mock records calls behind that signature. */
const transport = () => ({ post: post as VoiceApiTransport['post'] })

beforeEach(() => {
  post = vi.fn(async () => ({}))
  configureVoiceApi(transport(), '/voice')
})

test('start posts to the session start route with no body', async () => {
  await startVoiceSession('abc')

  expect(post).toHaveBeenCalledWith('/voice/abc/start')
})

test('end sends the runtime_session_id so the pipeline tears down immediately', async () => {
  await endVoiceSession('abc', { runtime_session_id: 'rt-1' })

  expect(post).toHaveBeenCalledWith('/voice/abc/end', {
    runtime_session_id: 'rt-1',
  })
})

test('end without a body stays a bodyless post — the request model is optional', async () => {
  await endVoiceSession('abc')

  expect(post).toHaveBeenCalledWith('/voice/abc/end', undefined)
})

test('the configured base path prefixes every route', async () => {
  configureVoiceApi(transport(), 'https://api.example.com/voice')

  await endVoiceSession('abc')

  expect(post).toHaveBeenCalledWith(
    'https://api.example.com/voice/abc/end',
    undefined,
  )
})
