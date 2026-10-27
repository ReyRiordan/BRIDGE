/**
 * Renders real golden-run frames. `foldTo(successRun, n)` gives a state that
 * the reducer actually produced, so these assertions describe the UI over the
 * same timeline the acceptance test locks — no bespoke hand-built states.
 *
 * No jest-dom: assertions use textContent / className / getAttribute.
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import GameScreen from './GameScreen'
import {
  foldTo,
  successRun,
  wardBaseState,
  wardEndRun,
} from '../state/__fixtures__/goldenRun'
import { WARD_BARRIER, WARD_KEY } from '../state/__fixtures__/scenario.fixture'

afterEach(() => {
  cleanup()
  // Vitest + React 19 will hang on the next file if fake timers leak.
  vi.useRealTimers()
})

const layerAttrs = () =>
  [...document.querySelectorAll('[data-layer]')].map((el) => [
    el.getAttribute('data-layer'),
    (el as HTMLElement).style.zIndex,
    el.getAttribute('data-src'),
  ])

describe('scene composite — scenario_3', () => {
  const ward = (n: number) => foldTo(wardEndRun, n, wardBaseState)

  it('draws the environment plate, the staff and the webp patient frame', () => {
    render(<GameScreen state={ward(1)} />)
    expect(layerAttrs()).toEqual([
      ['__environment__', '1', '/visuals/scenario_3/environment.webp'],
      [WARD_BARRIER, '2', ''],
      [WARD_KEY, '3', '/visuals/scenario_3/staff_present.webp'],
      ['__patient__', '4', '/visuals/scenario_3/patient_6.webp'],
    ])
  })

  it('closes the door UNDER the staff, then removes the staff for good', () => {
    const { rerender } = render(<GameScreen state={ward(4)} />)
    expect(layerAttrs()).toEqual([
      ['__environment__', '1', '/visuals/scenario_3/environment.webp'],
      [WARD_BARRIER, '2', '/visuals/scenario_3/door_closed.webp'],
      [WARD_KEY, '3', '/visuals/scenario_3/staff_present.webp'],
      ['__patient__', '4', '/visuals/scenario_3/patient_5.webp'],
    ])

    const staff = () =>
      document
        .querySelector(`[data-layer="${WARD_KEY}"]`)
        ?.getAttribute('data-src')
    rerender(<GameScreen state={ward(8)} />)
    expect(staff()).toBe('')
    // A later turn with no detections keeps them gone (the layer persists).
    rerender(<GameScreen state={ward(11)} />)
    expect(staff()).toBe('')
  })
})

describe('scene composite', () => {
  it('stacks the scenario layers back to front with ascending z', () => {
    render(<GameScreen state={foldTo(successRun, 1)} />)

    expect(layerAttrs()).toEqual([
      ['Environmental', '1', '/visuals/scenario_1/environment_default.png'],
      ['__patient__', '2', '/visuals/scenario_1/patient_5.png'],
      [
        'Caregiver involvement',
        '3',
        '/visuals/scenario_1/caregiver_default.png',
      ],
      // No inactive art → renders nothing.
      ['Force IV', '4', ''],
      ['Restraint', '5', ''],
    ])
  })

  it('lights a transient layer and clears it again', () => {
    const { rerender } = render(<GameScreen state={foldTo(successRun, 5)} />)
    const iv = () => document.querySelector('[data-layer="Force IV"]')
    expect(iv()?.getAttribute('data-src')).toBe(
      '/visuals/scenario_1/iv_active.png',
    )

    rerender(<GameScreen state={foldTo(successRun, 11)} />)
    expect(iv()?.getAttribute('data-src')).toBe('')
    // Mount-and-keep: the frame stays in the DOM, faded out, ready to return.
    expect(iv()?.querySelectorAll('img')).toHaveLength(1)
    expect(iv()?.querySelector('img')?.className).toContain('opacity-0')
  })

  it('follows escalation through the patient frames', () => {
    render(<GameScreen state={foldTo(successRun)} />)
    expect(
      document
        .querySelector('[data-layer="__patient__"]')
        ?.getAttribute('data-src'),
    ).toBe('/visuals/scenario_1/patient_0.png')
  })
})

describe('action badge', () => {
  it('shows the latest detection with its tone', () => {
    render(<GameScreen state={foldTo(successRun, 5)} />)
    const badge = screen.getByRole('status')
    expect(badge.textContent).toBe('Force IV: Attempt IV while agitated')
    expect(badge.getAttribute('data-tone')).toBe('bad')
  })

  it('remounts on a repeated action so the pop replays', () => {
    vi.useFakeTimers()
    // A Verbal Communication detection…
    const { rerender } = render(<GameScreen state={foldTo(successRun, 14)} />)
    act(() => vi.advanceTimersByTime(3000))
    expect(screen.queryByRole('status')).toBeNull()

    // …and the same action detected again much later: a new id, so it returns.
    rerender(<GameScreen state={foldTo(successRun, 22)} />)
    const badge = screen.getByRole('status')
    expect(badge.textContent).toContain('Verbal Communication')
    expect(badge.getAttribute('data-tone')).toBe('good')
  })

  it('auto-hides after 3 s', () => {
    vi.useFakeTimers()
    render(<GameScreen state={foldTo(successRun, 5)} />)
    expect(screen.queryByRole('status')).not.toBeNull()

    act(() => vi.advanceTimersByTime(2999))
    expect(screen.queryByRole('status')).not.toBeNull()

    act(() => vi.advanceTimersByTime(1))
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('escalation bar', () => {
  it('renders width, tone and aria from the scenario-derived state', () => {
    render(<GameScreen state={foldTo(successRun, 5)} />)
    const bar = screen.getByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('9')
    expect(bar.getAttribute('aria-valuemax')).toBe('10')
    expect(bar.getAttribute('data-tone')).toBe('crit')
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('90%')
    expect((bar.firstElementChild as HTMLElement).className).toContain(
      'bg-esc-crit',
    )
  })
})

describe('timer', () => {
  it('starts at the scenario time limit and goes urgent near the end', () => {
    const { rerender } = render(<GameScreen state={foldTo(successRun, 1)} />)
    const timer = () => screen.getByRole('timer')
    expect(timer().textContent).toBe('5:00')
    expect(timer().className).not.toContain('animate-pulse')

    const nearlyOver = {
      ...foldTo(successRun, 1),
      timer: { elapsed: 285, limit: 300 },
    }
    rerender(<GameScreen state={nearlyOver} />)
    expect(timer().textContent).toBe('0:15')
    expect(timer().className).toContain('animate-pulse')
  })
})

describe('transcript', () => {
  it('labels each side and waits only after the student speaks', () => {
    const { rerender } = render(<GameScreen state={foldTo(successRun, 4)} />)
    const roles = () =>
      [...document.querySelectorAll('[data-role]')].map((el) =>
        el.getAttribute('data-role'),
      )

    expect(roles()).toEqual(['student'])
    expect(screen.getByText('You')).toBeDefined()
    expect(screen.queryByTestId('waiting-indicator')).not.toBeNull()

    rerender(<GameScreen state={foldTo(successRun, 6)} />)
    expect(roles()).toEqual(['student', 'patient'])
    expect(screen.getByText('Patient')).toBeDefined()
    expect(screen.queryByTestId('waiting-indicator')).toBeNull()
  })
})

describe('mic status', () => {
  it('reads listening, and flips while the patient speaks', () => {
    const { rerender } = render(<GameScreen state={foldTo(successRun, 4)} />)
    const pill = () => document.querySelector('[data-mic]')
    expect(pill()?.getAttribute('data-mic')).toBe('listening')
    expect(pill()?.textContent).toContain('Listening')

    // The auto-mute is what actually silences the track; the pill only reports.
    rerender(
      <GameScreen state={foldTo(successRun, 4)} isAgentSpeaking isMuted />,
    )
    expect(pill()?.getAttribute('data-mic')).toBe('muted')
    expect(pill()?.textContent).toContain('Patient speaking')
  })
})
