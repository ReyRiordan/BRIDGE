/**
 * The lifecycle contract, driven through the real hook with the WebRTC service
 * singleton and the control-plane calls mocked.
 *
 * The service is mocked rather than the `useWebRTC` hook: the retry policy,
 * the drop watcher and the settle all key off state that hook derives (its
 * 1 s connection poll, its ICE-stall detection), and stubbing it out would
 * test the mock instead of the policy.
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import { useVoiceSession } from './useVoiceSession'
import { gameReducer, initialState, type GameState } from '../state/gameState'
import { scenarioFixture } from '../state/__fixtures__/scenario.fixture'
import { useEffect, useReducer } from 'react'

vi.mock('./voiceApi', () => ({
  startVoiceSession: vi.fn(),
  endVoiceSession: vi.fn(),
}))

vi.mock('./webrtc.service', () => ({
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

import { endVoiceSession, startVoiceSession } from './voiceApi'
import { webrtcService } from './webrtc.service'

const service = vi.mocked(webrtcService)
const startApi = vi.mocked(startVoiceSession)
const endApi = vi.mocked(endVoiceSession)

/** ICE-connect and connection-state polls both live on fake timers. */
const ice = (state: RTCIceConnectionState) =>
  service.getIceConnectionState.mockReturnValue(state)
const pc = (state: RTCPeerConnectionState | null) =>
  service.getConnectionState.mockReturnValue(state)

const startOk = (runtime = 'rt-1') =>
  startApi.mockResolvedValue({
    runtime_session_id: runtime,
    session_id: 's',
    ice_servers: [],
  })

interface Observed {
  begin: () => Promise<void>
  reset: () => void
  status: string
  connectionLost: boolean
  state: GameState
}

/** Latest hook + reducer output, published from an effect (never in render). */
let latest: Observed
const seen = (): Observed => latest

/** A miniature App: the same reducer, the same hook, no screens. */
function Harness() {
  const [state, dispatch] = useReducer(gameReducer, initialState)
  const voice = useVoiceSession(state, dispatch)
  useEffect(() => {
    latest = { ...voice, state }
  })
  return (
    <div data-phase={state.phase} data-status={voice.status}>
      <button
        onClick={() =>
          dispatch({ type: 'SCENARIO_LOADED', scenario: scenarioFixture })
        }
      >
        load
      </button>
    </div>
  )
}

/** Mount, and get as far as the intro phase (where Begin lives). */
async function mountAtIntro() {
  render(<Harness />)
  await act(async () => {
    screen.getByRole('button', { name: 'load' }).click()
  })
}

/** Drive `startCall`'s 250 ms ICE poll to completion. */
const flushIcePoll = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300)
  })
}

/** Push past the hook's 10 s ICE_STALL timeout. */
const flushIceStall = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10500)
  })
}

const beginNow = () => {
  void act(() => {
    void seen().begin()
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  service.requestMicrophonePermission.mockResolvedValue({} as MediaStream)
  service.initializeConnection.mockResolvedValue(undefined)
  service.closeConnection.mockResolvedValue(undefined)
  endApi.mockResolvedValue({ message: 'ok', transcript: [] })
  ice('new')
  pc('connected')
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

/** Begin, with ICE coming up on the first attempt. */
async function connect() {
  startOk()
  ice('connected')
  beginNow()
  await act(async () => {})
  await flushIcePoll()
}

describe('begin', () => {
  it('mints a session, starts the runtime and only then dispatches BEGIN', async () => {
    await mountAtIntro()
    startOk()
    ice('checking')
    beginNow()
    await act(async () => {})

    // Still connecting: the game screen must not appear before ICE is up.
    expect(seen().status).toBe('connecting')
    expect(seen().state.phase).toBe('intro')

    const sessionId = startApi.mock.calls[0][0]
    expect(sessionId).toMatch(/[0-9a-f-]{36}/)
    expect(service.initializeConnection).toHaveBeenCalledWith(
      sessionId,
      'rt-1',
      [],
      expect.any(Boolean),
    )

    ice('connected')
    await flushIcePoll()
    expect(seen().state.phase).toBe('game')
    expect(seen().status).toBe('active')
  })

  it('surfaces a mic denial and never calls /start', async () => {
    await mountAtIntro()
    service.requestMicrophonePermission.mockRejectedValue({
      code: 'MICROPHONE_PERMISSION_DENIED',
      message: 'denied',
    })
    beginNow()
    await act(async () => {})

    expect(startApi).not.toHaveBeenCalled()
    expect(seen().status).toBe('idle')
    expect(seen().state.phase).toBe('intro')
  })
})

describe('reconnect policy', () => {
  it('retries an ICE stall exactly once, with a fresh runtime_session_id', async () => {
    await mountAtIntro()
    startApi
      .mockResolvedValueOnce({
        runtime_session_id: 'rt-1',
        session_id: 's',
        ice_servers: [],
      })
      .mockResolvedValueOnce({
        runtime_session_id: 'rt-2',
        session_id: 's',
        ice_servers: [],
      })
    ice('checking')
    beginNow()
    await act(async () => {})
    await flushIceStall()

    // Second attempt: same session_id, brand-new runtime_session_id.
    expect(startApi).toHaveBeenCalledTimes(2)
    expect(startApi.mock.calls[0][0]).toBe(startApi.mock.calls[1][0])
    expect(service.initializeConnection).toHaveBeenLastCalledWith(
      expect.any(String),
      'rt-2',
      [],
      expect.any(Boolean),
    )
    expect(seen().status).toBe('connecting')

    // A second stall stops here — no third attempt, manual Retry from the UI.
    await flushIceStall()
    expect(startApi).toHaveBeenCalledTimes(2)
    expect(seen().status).toBe('idle')
    expect(seen().state.phase).toBe('intro')
  })

  it('does not retry a server-side failure', async () => {
    await mountAtIntro()
    startApi.mockRejectedValue(new Error('Request failed (502)'))
    beginNow()
    await act(async () => {})

    expect(startApi).toHaveBeenCalledTimes(1)
    expect(seen().status).toBe('idle')
  })
})

describe('end settle', () => {
  const gameOver = {
    v: 1 as const,
    type: 'game_over' as const,
    status: 'success' as const,
    reason: 'calm',
  }

  const fireGameOver = async () => {
    await act(async () => {
      service.onGameEvent?.(gameOver)
    })
  }

  const speak = async (speaking: boolean) => {
    await act(async () => {
      service.onRemoteAudioStateChange?.(speaking)
    })
  }

  it('waits for the final audio to start and finish before SHOW_END', async () => {
    await mountAtIntro()
    await connect()
    await fireGameOver()

    // game_over lands before the closing line: nothing has settled yet.
    expect(seen().state.phase).toBe('game')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    await speak(true)

    // Speaking: the rise window no longer applies.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(seen().state.phase).toBe('game')

    await speak(false)
    expect(seen().state.phase).toBe('end')
    expect(endApi).toHaveBeenCalledTimes(1)
    expect(endApi).toHaveBeenCalledWith(expect.any(String), {
      runtime_session_id: 'rt-1',
    })
  })

  it('settles on its own when no closing audio ever starts', async () => {
    await mountAtIntro()
    await connect()
    await fireGameOver()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2999)
    })
    expect(seen().state.phase).toBe('game')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2)
    })
    expect(seen().state.phase).toBe('end')
    expect(endApi).toHaveBeenCalledTimes(1)
  })

  it('hard-caps a patient line that never ends, well inside the 45 s grace', async () => {
    await mountAtIntro()
    await connect()
    await fireGameOver()
    await speak(true)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(19000)
    })
    expect(seen().state.phase).toBe('game')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(seen().state.phase).toBe('end')
    // Exactly once, even though rise/cap/drop could all have fired.
    expect(endApi).toHaveBeenCalledTimes(1)
  })

  it('treats a drop after game_over as a normal ending, not a lost connection', async () => {
    await mountAtIntro()
    await connect()
    await fireGameOver()

    pc('failed')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100)
    })

    expect(seen().connectionLost).toBe(false)
    expect(seen().state.phase).toBe('end')
    expect(endApi).toHaveBeenCalledTimes(1)
  })
})

describe('mid-game drop', () => {
  it('surfaces connection lost, ends once, and never silently resumes', async () => {
    await mountAtIntro()
    await connect()

    pc('disconnected')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100)
    })

    expect(seen().connectionLost).toBe(true)
    // The game is NOT over and NOT reset — no phase change, no reconnect.
    expect(seen().state.phase).toBe('game')
    expect(seen().state.gameOver).toBeNull()
    expect(startApi).toHaveBeenCalledTimes(1)
    expect(endApi).toHaveBeenCalledTimes(1)
  })
})

describe('reset', () => {
  it('mints a different session_id on the next begin', async () => {
    await mountAtIntro()
    await connect()
    const first = startApi.mock.calls[0][0]

    await act(async () => {
      seen().reset()
    })
    expect(seen().status).toBe('idle')
    expect(seen().connectionLost).toBe(false)

    await connect()
    const second = startApi.mock.calls[1][0]
    expect(second).not.toBe(first)
  })
})
