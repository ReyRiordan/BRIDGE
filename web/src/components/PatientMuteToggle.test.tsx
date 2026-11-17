import { act, cleanup, render, screen } from '@testing-library/react'
import PatientMuteToggle from './PatientMuteToggle'

afterEach(cleanup)

const toggle = () => screen.getByRole('button', { name: 'Mute patient audio' })

describe('PatientMuteToggle', () => {
  it('reports the state and toggles on click', () => {
    const onToggle = vi.fn()
    const { rerender } = render(
      <PatientMuteToggle muted={false} onToggle={onToggle} />,
    )
    expect(toggle().getAttribute('aria-pressed')).toBe('false')
    expect(toggle().textContent).toContain('Patient audio on')

    act(() => toggle().click())
    expect(onToggle).toHaveBeenCalledTimes(1)

    rerender(<PatientMuteToggle muted onToggle={onToggle} />)
    expect(toggle().getAttribute('aria-pressed')).toBe('true')
    expect(toggle().textContent).toContain('Patient muted')
  })
})
