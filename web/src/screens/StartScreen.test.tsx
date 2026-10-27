/**
 * The picker: the list loads on mount, one card per scenario with its intro
 * thumbnail, and a card click fetches that scenario's config before handing
 * it up. Both fetches fail into an inline alert with a Retry.
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import StartScreen from './StartScreen'
import {
  scenarioFixture,
  wardFixture,
} from '../state/__fixtures__/scenario.fixture'

const SUMMARIES = [scenarioFixture, wardFixture].map(
  ({ id, title, intro_image }) => ({ id, title, intro_image }),
)

const json = (body: unknown) => ({
  ok: true,
  status: 200,
  json: async () => body,
})
const fail = (status: number) => ({
  ok: false,
  status,
  json: async () => ({}),
})

function stubFetch(route: (url: string) => unknown) {
  const fetchMock = vi.fn(async (url: string) => route(url))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const byId: Record<string, unknown> = {
  '/scenario/scenario_1': scenarioFixture,
  '/scenario/scenario_3': wardFixture,
}

const routes = (url: string) =>
  url === '/scenarios' ? json(SUMMARIES) : json(byId[url])

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const settle = () => act(async () => {})
const card = (id: string) =>
  document.querySelector<HTMLButtonElement>(`[data-scenario="${id}"]`)

describe('StartScreen', () => {
  it('lists every scenario as a card with its intro thumbnail', async () => {
    stubFetch(routes)
    render(<StartScreen onLoaded={() => {}} />)
    expect(screen.getByText('Loading scenarios…')).toBeDefined()
    await settle()

    const cards = [...document.querySelectorAll('[data-scenario]')]
    expect(cards.map((c) => c.getAttribute('data-scenario'))).toEqual([
      'scenario_1',
      'scenario_3',
    ])
    expect(card('scenario_3')?.textContent).toContain(wardFixture.title)
    expect(card('scenario_3')?.querySelector('img')?.getAttribute('src')).toBe(
      '/visuals/scenario_3/intro.webp',
    )
    expect(card('scenario_1')?.querySelector('img')?.getAttribute('src')).toBe(
      '/visuals/scenario_1/intro.jpg',
    )
  })

  it('fetches the chosen scenario and hands it up', async () => {
    const fetchMock = stubFetch(routes)
    const onLoaded = vi.fn()
    render(<StartScreen onLoaded={onLoaded} />)
    await settle()

    card('scenario_3')?.click()
    await settle()

    expect(fetchMock).toHaveBeenLastCalledWith('/scenario/scenario_3')
    expect(onLoaded).toHaveBeenCalledWith(wardFixture)
  })

  it('offers Retry when the list fails, and recovers', async () => {
    let up = false
    stubFetch((url) => (up ? routes(url) : fail(503)))
    render(<StartScreen onLoaded={() => {}} />)
    await settle()

    expect(screen.getByRole('alert').textContent).toContain('503')
    up = true
    screen.getByRole('button', { name: 'Retry' }).click()
    await settle()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(card('scenario_1')).not.toBeNull()
  })

  it('retries the same card when its config fails', async () => {
    let up = false
    const fetchMock = stubFetch((url) =>
      url === '/scenarios' || up ? routes(url) : fail(500),
    )
    const onLoaded = vi.fn()
    render(<StartScreen onLoaded={onLoaded} />)
    await settle()

    card('scenario_3')?.click()
    await settle()
    expect(screen.getByRole('alert').textContent).toContain('500')
    expect(onLoaded).not.toHaveBeenCalled()

    up = true
    screen.getByRole('button', { name: 'Retry' }).click()
    await settle()
    expect(fetchMock).toHaveBeenLastCalledWith('/scenario/scenario_3')
    expect(onLoaded).toHaveBeenCalledWith(wardFixture)
  })

  it('rejects a list that does not match the contract', async () => {
    stubFetch(() => json([{ id: 'scenario_1' }]))
    render(<StartScreen onLoaded={() => {}} />)
    await settle()
    expect(screen.getByRole('alert').textContent).toContain('did not match')
  })
})
