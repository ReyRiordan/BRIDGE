import { createSession } from './sessions'

const BODY = {
  netid: 'abc123',
  scenario_id: 'scenario_1',
  mode: 'voice' as const,
}

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(
    async () => new Response(JSON.stringify(body), { status }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createSession', () => {
  it('posts the identity and scenario and returns the issued id', async () => {
    const fetchMock = stubFetch(200, { session_id: 'scenario_1--abc' })
    await expect(createSession(BODY)).resolves.toEqual({
      session_id: 'scenario_1--abc',
    })
    expect(fetchMock).toHaveBeenCalledWith('/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(BODY),
    })
  })

  it('throws on an HTTP error', async () => {
    stubFetch(422, { detail: 'Enter a valid NetID.' })
    await expect(createSession(BODY)).rejects.toThrow('HTTP 422')
  })

  it('rejects an id for another scenario or of the wrong type', async () => {
    stubFetch(200, { session_id: 'scenario_2--abc' })
    await expect(createSession(BODY)).rejects.toThrow('did not match')
    stubFetch(200, { session_id: 7 })
    await expect(createSession(BODY)).rejects.toThrow('did not match')
  })
})
