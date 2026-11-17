import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import EscalationChart from './EscalationChart'
import { record } from './fixtures'

afterEach(cleanup)

function draw() {
  const r = record()
  return render(
    <EscalationChart
      timeline={r.timeline}
      turns={r.turns}
      max={10}
      goal={0}
      timeLimit={300}
      endT={212.4}
    />,
  )
}

describe('EscalationChart', () => {
  it('draws one marker per timeline step, toned by what caused it', () => {
    const { container } = draw()
    const markers = [...container.querySelectorAll('[data-marker]')]
    expect(markers.map((m) => m.getAttribute('data-tone'))).toEqual([
      'helpful',
      'inactivity',
      'helpful',
    ])
    expect(markers[0].querySelector('.fill-good')).not.toBeNull()
    expect(markers[1].querySelector('.stroke-ink-muted')).not.toBeNull()
  })

  it('marks an escalating step red', () => {
    const r = record()
    const { container } = render(
      <EscalationChart
        timeline={[
          { t: 0, escalation: 5, cause: 'start' },
          { t: 9, escalation: 7, cause: 'turn' },
        ]}
        turns={r.turns}
        max={10}
        goal={0}
        timeLimit={300}
      />,
    )
    const marker = container.querySelector('[data-marker]')!
    expect(marker.getAttribute('data-tone')).toBe('escalating')
    expect(marker.querySelector('.fill-bad')).not.toBeNull()
  })

  it('renders dashed goal and max lines', () => {
    const { container } = draw()
    for (const ref of ['goal', 'max']) {
      const line = container.querySelector(`[data-ref="${ref}"] line`)!
      expect(line.getAttribute('stroke-dasharray')).toBe('4 4')
    }
  })

  it('shows the turn behind a marker on focus', () => {
    const { container } = draw()
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.focus(container.querySelectorAll('[data-marker]')[2])
    const tip = screen.getByRole('tooltip')
    expect(tip.textContent).toContain('00:40')
    expect(tip.textContent).toContain('Explain risks −2')
    expect(tip.textContent).toContain('You could get very sick.')
  })
})
