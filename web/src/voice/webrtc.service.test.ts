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
interface FakeChannel {
  onmessage: ((event: MessageEvent) => void) | null
  readyState: RTCDataChannelState
  sent: string[]
  send: (data: string) => void
}
let channels: FakeChannel[] = []
let tracks = 0
let transceivers: [string, RTCRtpTransceiverInit | undefined][] = []
let closed = 0
let pcs: FakePeerConnection[] = []

class FakePeerConnection {
  iceGatheringState = 'complete'
  localDescription = { sdp: 'offer-sdp', type: 'offer' }
  ontrack: unknown = null

  constructor(config: RTCConfiguration) {
    configs.push(config)
    pcs.push(this)
  }

  addTrack() {
    tracks += 1
  }
  addTransceiver(kind: string, init?: RTCRtpTransceiverInit) {
    transceivers.push([kind, init])
  }
  createDataChannel() {
    const channel: FakeChannel = {
      onmessage: null,
      readyState: 'open',
      sent: [],
      send(data: string) {
        this.sent.push(data)
      },
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
  tracks = 0
  transceivers = []
  pcs = []
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
  // The service requests a mic before negotiating; hand it a stream with one
  // track so the voice path's addTrack is observable.
  getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop() {} }] }))
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
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

let getUserMedia: ReturnType<typeof vi.fn>

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

test('a stall with candidates already gathered signals them instead of failing', async () => {
  // What the deployed reality looks like: KVS's relay candidates arrive in
  // ~100 ms, then `complete` never comes because the unreachable members of
  // each stun/turn/turns triple keep the phase open.
  vi.useFakeTimers()
  const gatheredSdp = 'offer-sdp\r\na=candidate:1 1 udp 1 1.2.3.4 443 typ relay'
  class StallingPeerConnection extends FakePeerConnection {
    iceGatheringState = 'gathering'
    localDescription = { sdp: gatheredSdp, type: 'offer' }
  }
  vi.stubGlobal('RTCPeerConnection', StallingPeerConnection)

  const connect = webrtcService.initializeConnection('s', 'r', TURN)
  await vi.advanceTimersByTimeAsync(10000)
  await connect

  expect(voiceApi.signalVoiceSession).toHaveBeenCalledWith('s', {
    runtime_session_id: 'r',
    sdp: gatheredSdp,
    type: 'offer',
  })

  vi.useRealTimers()
})

describe('text mode (sendAudio = false)', () => {
  test('never asks for the mic and offers a recvonly audio transceiver', async () => {
    await webrtcService.initializeConnection('s', 'r', TURN, true, false)

    expect(getUserMedia).not.toHaveBeenCalled()
    expect(tracks).toBe(0)
    expect(transceivers).toEqual([['audio', { direction: 'recvonly' }]])
    // Relay-only still applies: the text path goes through the same TURN.
    expect(configs[0].iceTransportPolicy).toBe('relay')
  })

  test('voice mode is unchanged: mic track, no transceiver', async () => {
    await webrtcService.initializeConnection('s', 'r', TURN)

    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(tracks).toBe(1)
    expect(transceivers).toEqual([])
  })
})

describe('sendAppMessage', () => {
  test('sends JSON on an open channel', async () => {
    await webrtcService.initializeConnection('s', 'r', TURN, true, false)
    const payload = { v: 1, type: 'student_text', content: 'hi' }

    expect(webrtcService.sendAppMessage(payload)).toBe(true)
    expect(channels[0].sent).toEqual([JSON.stringify(payload)])
  })

  test('returns false on a channel that is not open', async () => {
    await webrtcService.initializeConnection('s', 'r', TURN, true, false)
    channels[0].readyState = 'connecting'

    expect(webrtcService.sendAppMessage({ v: 1 })).toBe(false)
    expect(channels[0].sent).toEqual([])
  })

  test('returns false with no connection at all', async () => {
    await webrtcService.closeConnection()
    expect(webrtcService.sendAppMessage({ v: 1 })).toBe(false)
  })
})

test('patient mute applies to the audio element, including one created later', async () => {
  const created: { muted: boolean }[] = []
  vi.stubGlobal(
    'Audio',
    class {
      muted = false
      srcObject: unknown = null
      constructor() {
        created.push(this)
      }
      play = async () => {}
      pause() {}
    },
  )
  vi.stubGlobal(
    'AudioContext',
    class {
      createMediaStreamSource() {
        return { connect() {} }
      }
      createAnalyser() {
        return { fftSize: 0, frequencyBinCount: 1, getByteFrequencyData() {} }
      }
      async close() {}
    },
  )
  vi.stubGlobal('requestAnimationFrame', () => 0)
  vi.stubGlobal('cancelAnimationFrame', () => {})

  await webrtcService.initializeConnection('s', 'r', TURN, true, false)
  // Muted before the patient's track arrives: it must still come up muted.
  webrtcService.setRemoteAudioMuted(true)
  const ontrack = pcs[0].ontrack as (e: { streams: unknown[] }) => void
  ontrack({ streams: [{}] })
  expect(created[0].muted).toBe(true)

  webrtcService.setRemoteAudioMuted(false)
  expect(created[0].muted).toBe(false)
})
