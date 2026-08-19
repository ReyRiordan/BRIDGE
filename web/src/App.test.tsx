/**
 * The shell flow, driven the way a user drives it: Start fetches `/scenario`
 * and every subsequent screen is derived from that payload.
 *
 * The end phase is not reachable by clicking during [Rewrite F] — nothing
 * dispatches wire events until [Rewrite G2] mounts the voice session. The
 * overlay composition is covered in EndScreen.test.tsx, and the transition
 * itself in gameReducer.test.ts.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { scenarioFixture } from './state/__fixtures__/scenario.fixture'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
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

    screen.getByRole('button', { name: 'Begin Simulation' }).click()

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
