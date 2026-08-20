/**
 * The shell flow, driven the way a user drives it: Start fetches `/scenario`,
 * Begin connects a (mocked) voice session, and every subsequent screen is
 * derived from the payload plus the events that arrive on the data channel.
 *
 * The WebRTC service singleton is mocked; everything above it — the session
 * hook, the reducer, the screens — is real.
 */
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { scenarioFixture } from './state/__fixtures__/scenario.fixture'
import { successRun } from './state/__fixtures__/goldenRun'

vi.mock('./voice/voiceApi', () => ({
  startVoiceSession: vi.fn(),
  endVoiceSession: vi.fn(),
}))

vi.mock('./voice/webrtc.service', () => ({
  webrtcService: {
    onRemoteAudioStateChange: null,
    onGameEvent: null,
    requestMicrophonePermission: vi.fn(),
    initializeConnection: vi.fn(),
    closeConnection: vi.fn(),
    setMuted: vi.fn(),
    getConnectionState: vi.fn(),
    getIceConnectionState: vi.fn(),
  },
}))

import { endVoiceSession, startVoiceSession } from './voice/voiceApi'
import { webrtcService } from './voice/webrtc.service'

const service = vi.mocked(webrtcService)
const startApi = vi.mocked(startVoiceSession)
const endApi = vi.mocked(endVoiceSession)

beforeEach(() => {
  vi.clearAllMocks()
  service.requestMicrophonePermission.mockResolvedValue({} as MediaStream)
  service.initializeConnection.mockResolvedValue(undefined)
  service.closeConnection.mockResolvedValue(undefined)
  service.getIceConnectionState.mockReturnValue('connected')
  service.getConnectionState.mockReturnValue('connected')
  startApi.mockResolvedValue({
    runtime_session_id: 'rt-1',
    session_id: 's',
    ice_servers: [],
  })
  endApi.mockResolvedValue({ message: 'ok', transcript: [] })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const stubFetch = (impl: () => Promise<unknown>) =>
  vi.stubGlobal(
    'fetch',
    vi.fn(() => impl()),
  )

const ok = () =>
  stubFetch(async () => ({
    ok: true,
    status: 200,
    json: async () => scenarioFixture,
  }))

describe('App', () => {
  it('walks Start → Intro → Game off the real /scenario payload', async () => {
    ok()
    render(<App />)

    expect(screen.getByRole('heading', { name: 'BRIDGE' })).toBeDefined()
    screen.getByRole('button', { name: 'Start' }).click()

    await waitFor(() =>
      screen.getByRole('button', { name: 'Begin Simulation' }),
    )
    expect(screen.getByText(scenarioFixture.intro)).toBeDefined()
    expect(screen.getByText(scenarioFixture.goal)).toBeDefined()

    await beginSimulation()

    // The opening frame is entirely scenario-derived: start 5 of max 10, and a
    // 300 s limit.
    const bar = await screen.findByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('5')
    expect(bar.getAttribute('aria-valuemax')).toBe('10')
    expect(screen.getByRole('timer').textContent).toBe('5:00')
    expect(
      document
        .querySelector('[data-layer="__patient__"]')
        ?.getAttribute('data-src'),
    ).toBe('/visuals/patient_5.png')
  })

  it('stays on Start and offers Retry when the fetch fails', async () => {
    stubFetch(async () => ({ ok: false, status: 503, json: async () => ({}) }))
    render(<App />)
    screen.getByRole('button', { name: 'Start' }).click()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('503')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeDefined()

    // Retry succeeds once the API is back.
    ok()
    screen.getByRole('button', { name: 'Retry' }).click()
    await waitFor(() =>
      screen.getByRole('button', { name: 'Begin Simulation' }),
    )
  })

  it('rejects a body that does not match the scenario contract', async () => {
    stubFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ intro: 'x' }),
    }))
    render(<App />)
    screen.getByRole('button', { name: 'Start' }).click()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('did not match')
  })
})

/** Click Begin and let the (mocked) connect + 250 ms ICE poll finish. */
async function beginSimulation() {
  screen.getByRole('button', { name: 'Begin Simulation' }).click()
  if (vi.isFakeTimers()) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400)
    })
  } else {
    await waitFor(() => screen.getByRole('progressbar'))
  }
}

/** Push one wire event down the data channel, exactly as the service would. */
async function wire(event: unknown) {
  await act(async () => {
    service.onGameEvent?.(event)
  })
}

describe('a full session', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ok()
  })

  it('plays a game end to end and starts a fresh session on Play Again', async () => {
    render(<App />)
    screen.getByRole('button', { name: 'Start' }).click()
    await act(async () => {})
    await beginSimulation()

    const firstSessionId = startApi.mock.calls[0][0]
    expect(screen.getByRole('progressbar')).toBeDefined()

    // The data channel drives everything on screen.
    for (const event of successRun) await wire(event)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe(
      '0',
    )
    expect(
      [...document.querySelectorAll('[data-role]')].length,
    ).toBeGreaterThan(0)

    // game_over is in the run, but the debrief waits on the closing audio.
    expect(screen.queryByRole('dialog')).toBeNull()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100)
    })

    const dialog = screen.getByRole('dialog')
    expect(dialog).toBeDefined()
    expect(endApi).toHaveBeenCalledTimes(1)
    expect(endApi).toHaveBeenCalledWith(firstSessionId, {
      runtime_session_id: 'rt-1',
    })

    // Play Again: back to Start, then a brand-new session on a clean board.
    screen.getByRole('button', { name: 'Play Again' }).click()
    await act(async () => {})
    screen.getByRole('button', { name: 'Start' }).click()
    await act(async () => {})
    await beginSimulation()

    expect(startApi.mock.calls[1][0]).not.toBe(firstSessionId)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe(
      '5',
    )
    expect(screen.getByRole('timer').textContent).toBe('5:00')
    expect(document.querySelectorAll('[data-role]')).toHaveLength(0)
  })

  it('shows connection lost — never a game fail — when the call drops mid-game', async () => {
    render(<App />)
    screen.getByRole('button', { name: 'Start' }).click()
    await act(async () => {})
    await beginSimulation()

    service.getConnectionState.mockReturnValue('failed')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100)
    })

    expect(document.getElementById('end-title')?.textContent).toBe(
      'Connection Lost',
    )
    expect(endApi).toHaveBeenCalledTimes(1)
  })

  it('keeps the student on Intro with a Retry when connecting fails', async () => {
    startApi.mockRejectedValue(new Error('Request failed (502)'))
    render(<App />)
    screen.getByRole('button', { name: 'Start' }).click()
    await act(async () => {})
    await beginSimulation()

    expect(screen.getByRole('alert').textContent).toContain('502')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeDefined()
    expect(screen.queryByRole('progressbar')).toBeNull()
  })
})
