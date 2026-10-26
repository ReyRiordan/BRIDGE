import { cleanup, render, screen } from '@testing-library/react'
import EndScreen from './EndScreen'
import GameScreen from './GameScreen'
import { selectChecklist } from '../state/useGame'
import {
  failRun,
  foldTo,
  successRun,
  wardBaseState,
  wardEndRun,
} from '../state/__fixtures__/goldenRun'
import {
  WARD_BARRIER,
  WARD_DEBRIEF,
  WARD_END,
  WARD_KEY,
} from '../state/__fixtures__/scenario.fixture'

afterEach(cleanup)

const renderFor = (run: typeof successRun, onPlayAgain = () => {}) => {
  const state = foldTo(run)
  return render(
    <EndScreen
      gameOver={state.gameOver!}
      checklist={selectChecklist(state)}
      onPlayAgain={onPlayAgain}
    />,
  )
}

describe('EndScreen', () => {
  it('titles and tones by status', () => {
    renderFor(successRun)
    const title = screen.getByRole('heading', { level: 2 })
    expect(title.textContent).toBe('De-escalation Successful!')
    expect(title.className).toContain('text-good')
    expect(
      screen.getByText('The patient is calm and ready to continue care.'),
    ).toBeDefined()

    cleanup()
    renderFor(failRun)
    const failTitle = screen.getByRole('heading', { level: 2 })
    expect(failTitle.textContent).toBe('Simulation Ended')
    expect(failTitle.className).toContain('text-bad')
  })

  it('is a modal dialog', () => {
    renderFor(successRun)
    const dialog = screen.getByRole('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(document.activeElement?.textContent).toBe('Play Again')
  })

  it('classifies every action as found-good, found-bad or missed', () => {
    renderFor(successRun)
    const rows = [...document.querySelectorAll('[data-action]')].map((el) => [
      el.getAttribute('data-action'),
      el.getAttribute('data-status'),
    ])
    expect(rows).toEqual([
      ['Caregiver involvement', 'found-good'],
      ['Environmental', 'found-good'],
      ['Verbal Communication', 'found-good'],
      ['Offer Control', 'found-good'],
      ['Acknowledge distress', 'found-good'],
      ['Delay Procedure', 'missed'],
      // Taken, but it escalates — found, and wrong.
      ['Force IV', 'found-bad'],
      ['Authoritative tone', 'missed'],
      ['Restraint', 'missed'],
    ])
    expect(screen.getByText('+4')).toBeDefined()
    expect(screen.getByText('-3')).toBeDefined()
  })

  it('renders the connection-lost variant without game fail copy', () => {
    const state = foldTo(successRun)
    render(
      <EndScreen
        gameOver={{
          status: 'connection_lost',
          reason: 'Connection lost — the session could not continue.',
        }}
        checklist={selectChecklist(state)}
        onPlayAgain={() => {}}
      />,
    )
    const title = screen.getByRole('heading', { level: 2 })
    expect(title.textContent).toBe('Connection Lost')
    expect(title.textContent).not.toBe('Simulation Ended')
    // Same way out as any other ending.
    expect(screen.getByRole('button', { name: 'Play Again' })).toBeDefined()
  })

  it('reports Play Again', () => {
    const onPlayAgain = vi.fn()
    renderFor(successRun, onPlayAgain)
    screen.getByRole('button', { name: 'Play Again' }).click()
    expect(onPlayAgain).toHaveBeenCalledTimes(1)
  })

  it('overlays the frozen final scene rather than replacing it', () => {
    const state = foldTo(successRun)
    render(
      <>
        <GameScreen state={{ ...state, phase: 'end' }} />
        <EndScreen
          gameOver={state.gameOver!}
          checklist={selectChecklist(state)}
          onPlayAgain={() => {}}
        />
      </>,
    )
    // The debrief and the scene that produced it are on screen together.
    expect(screen.getByRole('dialog')).toBeDefined()
    expect(
      document
        .querySelector('[data-layer="__patient__"]')
        ?.getAttribute('data-src'),
    ).toBe('/visuals/scenario_1/patient_0.png')
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe(
      '0',
    )
  })
})

describe('EndScreen — END SIMULATION', () => {
  const state = foldTo(wardEndRun, wardEndRun.length, wardBaseState)

  it('shows the debrief as the reason and the END row as bad', () => {
    render(
      <EndScreen
        gameOver={state.gameOver!}
        checklist={selectChecklist(state)}
        onPlayAgain={() => {}}
      />,
    )
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe(
      'Simulation Ended',
    )
    expect(screen.getByText(WARD_DEBRIEF)).toBeDefined()

    const row = (type: string) =>
      document.querySelector(`[data-action="${type}"]`)
    expect(row(WARD_END)?.getAttribute('data-status')).toBe('found-bad')
    expect(row(WARD_END)?.textContent).toContain('END')
    expect(row(WARD_KEY)?.getAttribute('data-status')).toBe('found-good')
    // The conditional row: with-KEY value, plus the muted without note.
    expect(row(WARD_BARRIER)?.textContent).toContain('-2')
    expect(row(WARD_BARRIER)?.textContent).toContain(`(-1 without ${WARD_KEY})`)
  })
})
