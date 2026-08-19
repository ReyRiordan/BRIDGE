import { cleanup, render, screen } from '@testing-library/react'
import EndScreen from './EndScreen'
import GameScreen from './GameScreen'
import { selectChecklist } from '../state/useGame'
import { failRun, foldTo, successRun } from '../state/__fixtures__/goldenRun'

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
        <GameScreen
          state={{ ...state, phase: 'end' }}
          onGameOverSettled={() => {}}
        />
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
    ).toBe('/visuals/patient_0.png')
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe(
      '0',
    )
  })
})
