import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import ResultsApp from './ResultsApp'
import { SCENARIO, record, summary } from './fixtures'

vi.mock('../api/scenario', () => ({ fetchScenario: vi.fn() }))
vi.mock('./api', async (importOriginal) => {
  const real = await importOriginal<typeof import('./api')>()
  return {
    ...real,
    login: vi.fn(),
    listSessions: vi.fn(),
    getSession: vi.fn(),
  }
})
import { fetchScenario } from '../api/scenario'
import { Unauthorized, getSession, listSessions, login } from './api'

function go(hash: string) {
  act(() => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

beforeEach(() => {
  sessionStorage.clear()
  window.location.hash = '#/results'
  vi.mocked(listSessions).mockResolvedValue([summary()])
  vi.mocked(getSession).mockResolvedValue(record())
  vi.mocked(fetchScenario).mockResolvedValue(SCENARIO)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ResultsApp', () => {
  it('shows the login form without a token, and the list after signing in', async () => {
    vi.mocked(login).mockResolvedValue('tok-1')
    render(<ResultsApp />)
    expect(screen.getByLabelText('Password')).toBeDefined()
    expect(listSessions).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'local' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('test01')).toBeDefined()
    expect(login).toHaveBeenCalledWith('local')
    expect(listSessions).toHaveBeenCalledWith('tok-1')
    expect(sessionStorage.getItem('bridge.adminToken')).toBe('tok-1')
  })

  it('shows a wrong password inline', async () => {
    vi.mocked(login).mockRejectedValue(new Unauthorized('Wrong password.'))
    render(<ResultsApp />)
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'nope' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Wrong password.',
    )
  })

  it('drops the token and returns to login on a 401', async () => {
    sessionStorage.setItem('bridge.adminToken', 'stale')
    vi.mocked(listSessions).mockRejectedValue(new Unauthorized())
    render(<ResultsApp />)
    expect(await screen.findByLabelText('Password')).toBeDefined()
    expect(screen.getByText(/sign-in has expired/)).toBeDefined()
    expect(sessionStorage.getItem('bridge.adminToken')).toBeNull()
  })

  it('navigates list ↔ detail on the hash', async () => {
    sessionStorage.setItem('bridge.adminToken', 'tok-1')
    const { container } = render(<ResultsApp />)
    await screen.findByText('test01')

    fireEvent.click(
      container.querySelector(
        'tr[data-session="scenario_1--a"] td:nth-child(3)',
      )!,
    )
    expect(window.location.hash).toBe('#/results/scenario_1--a')
    act(() => window.dispatchEvent(new HashChangeEvent('hashchange')))
    expect(await screen.findByText('Transcript')).toBeDefined()
    expect(getSession).toHaveBeenCalledWith('tok-1', 'scenario_1--a')
    expect(fetchScenario).toHaveBeenCalledWith('scenario_1')
    expect(await screen.findByText(/Earned at 00:12/)).toBeDefined()

    go('#/results')
    expect(
      await screen.findByRole('button', { name: 'Download CSV' }),
    ).toBeDefined()
    // The list is kept across navigation, not refetched.
    expect(listSessions).toHaveBeenCalledTimes(1)
  })

  it('returns to login when the detail fetch is a 401', async () => {
    sessionStorage.setItem('bridge.adminToken', 'tok-1')
    vi.mocked(getSession).mockRejectedValue(new Unauthorized())
    go('#/results/scenario_1--a')
    render(<ResultsApp />)
    expect(await screen.findByLabelText('Password')).toBeDefined()
  })
})
