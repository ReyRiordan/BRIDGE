/**
 * The relay-only seam: `iceTransportPolicy` and the empty-ICE-servers warning.
 *
 * Relay-only is the default and the deployed reality; local dev is the single
 * caller that opts out. A stubbed RTCPeerConnection records the config so the
 * assertion is on what the browser would actually be handed.
 */

import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { webrtcService } from './webrtc.service'
import * as voiceApi from './voiceApi'

let configs: RTCConfiguration[] = []

class FakePeerConnection {
  iceGatheringState = 'complete'
  localDescription = { sdp: 'offer-sdp', type: 'offer' }
  ontrack: unknown = null

  constructor(config: RTCConfiguration) {
    configs.push(config)
  }

  addTrack() {}
  createDataChannel() {
    return { onmessage: null }
  }
  async createOffer() {
    return { sdp: 'offer-sdp', type: 'offer' }
  }
  async setLocalDescription() {}
  async setRemoteDescription() {}
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

beforeEach(() => {
  configs = []
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
