/**
 * Three seams: `iceTransportPolicy` (relay-only is the default and the deployed
 * reality; local dev is the single caller that opts out), the data channel that
 * carries the v1 game events, and the gathering-stall timeout.
 *
 * A stubbed RTCPeerConnection records the config so the assertion is on what
 * the browser would actually be handed.
 */

import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { webrtcService } from './webrtc.service'
import * as voiceApi from './voiceApi'

let configs: RTCConfiguration[] = []
let channels: { onmessage: ((event: MessageEvent) => void) | null }[] = []
let closed = 0

class FakePeerConnection {
  iceGatheringState = 'complete'
  localDescription = { sdp: 'offer-sdp', type: 'offer' }
  ontrack: unknown = null

  constructor(config: RTCConfiguration) {
    configs.push(config)
  }

  addTrack() {}
  createDataChannel() {
    const channel: { onmessage: ((event: MessageEvent) => void) | null } = {
      onmessage: null,
    }
    channels.push(channel)
    return channel
  }
  async createOffer() {
    return { sdp: 'offer-sdp', type: 'offer' }
  }
  async setLocalDescription() {}
  async setRemoteDescription() {}
  addEventListener() {}
  removeEventListener() {}
  close() {
    closed += 1
  }
}

beforeEach(() => {
  configs = []
  channels = []
  closed = 0
  vi.stubGlobal('RTCPeerConnection', FakePeerConnection)
  vi.stubGlobal(
    'RTCSessionDescription',
    class {
      constructor(init: unknown) {
        Object.assign(this, init)
      }
    },
  )
  // The service requests a mic before negotiating; hand it a stream with no
  // tracks so nothing else has to be faked.
  vi.stubGlobal('navigator', {
    mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) },
  })
  vi.spyOn(voiceApi, 'signalVoiceSession').mockResolvedValue({
    sdp: 'answer-sdp',
    type: 'answer',
  })
})

afterEach(async () => {
  await webrtcService.closeConnection()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const TURN = [{ urls: ['turn:example'], username: 'u', credential: 'c' }]

test('defaults to relay-only', async () => {
  await webrtcService.initializeConnection('s', 'r', TURN)

  expect(configs[0].iceTransportPolicy).toBe('relay')
})

test('relayOnly=false opens ICE to all candidates (local dev only)', async () => {
  await webrtcService.initializeConnection('s', 'r', [], false)

  expect(configs[0].iceTransportPolicy).toBe('all')
})

test('warns about empty ICE servers only under relay-only', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

  await webrtcService.initializeConnection('s', 'r', [], true)
  expect(warn).toHaveBeenCalledTimes(1)

  warn.mockClear()
  // Empty is the EXPECTED state locally — warning there is just noise.
  await webrtcService.initializeConnection('s', 'r', [], false)
  expect(warn).not.toHaveBeenCalled()
})

/** Feed one raw data-channel payload through the service's handler. */
const send = (data: unknown) =>
  channels[0].onmessage?.({ data } as MessageEvent)

const EVENTS = [
  {
    v: 1,
    type: 'transcript_update',
    role: 'student',
    content: 'hi',
    timestamp: 't',
  },
  {
    v: 1,
    type: 'state_update',
    escalation: 6,
    max: 10,
    active_actions: [],
    status: 's',
  },
  {
    v: 1,
    type: 'action_detected',
    action_type: 'a',
    desc: 'd',
    point_change: -1,
  },
  { v: 1, type: 'timer', elapsed: 3, limit: 300 },
  { v: 1, type: 'game_over', status: 'success', reason: 'calm' },
]

test('every v1 game event reaches onGameEvent as a parsed object', async () => {
  const received: unknown[] = []
  webrtcService.onGameEvent = (event) => received.push(event)

  await webrtcService.initializeConnection('s', 'r', TURN)
  EVENTS.forEach((event) => send(JSON.stringify(event)))

  expect(received).toEqual(EVENTS)
  webrtcService.onGameEvent = null
})

test('unparseable payloads are dropped, not forwarded, and never throw', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const received: unknown[] = []
  webrtcService.onGameEvent = (event) => received.push(event)

  await webrtcService.initializeConnection('s', 'r', TURN)
  expect(() => send('not json {')).not.toThrow()
  // Binary frames never reach JSON.parse at all.
  send(new ArrayBuffer(4))

  expect(received).toEqual([])
  expect(warn).toHaveBeenCalled()
  webrtcService.onGameEvent = null
})

test('nothing is filtered by shape — the reducer is the single validation point', async () => {
  const received: unknown[] = []
  webrtcService.onGameEvent = (event) => received.push(event)

  await webrtcService.initializeConnection('s', 'r', TURN)
  send(JSON.stringify({ v: 2, type: 'state_update' }))
  send(JSON.stringify({ v: 1, type: 'not_a_real_event' }))

  expect(received).toHaveLength(2)
  webrtcService.onGameEvent = null
})

test('a gathering stall rejects as CONNECTION_FAILED instead of hanging', async () => {
  vi.useFakeTimers()
  class StallingPeerConnection extends FakePeerConnection {
    iceGatheringState = 'gathering'
  }
  vi.stubGlobal('RTCPeerConnection', StallingPeerConnection)

  const connect = webrtcService.initializeConnection('s', 'r', TURN)
  const assertion = expect(connect).rejects.toMatchObject({
    code: 'CONNECTION_FAILED',
    message: expect.stringContaining('gathering'),
  })
  await vi.advanceTimersByTimeAsync(10000)
  await assertion
  // The failed attempt tore its peer connection down rather than orphaning it.
  expect(closed).toBeGreaterThan(0)

  vi.useRealTimers()
})
