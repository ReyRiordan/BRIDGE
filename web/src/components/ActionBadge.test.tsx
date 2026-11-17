import { render, screen } from '@testing-library/react'
import ActionBadge from './ActionBadge'

const renderBadge = (pointChange: number, repeat = false) =>
  render(
    <ActionBadge
      actionType="Offer control"
      desc="Give the patient a choice"
      pointChange={pointChange}
      repeat={repeat}
    />,
  )

describe('ActionBadge', () => {
  it('shows a helpful action in the good tone', () => {
    renderBadge(-1)
    const badge = screen.getByRole('status')
    expect(badge.getAttribute('data-tone')).toBe('good')
    expect(badge.textContent).toBe('Offer control: Give the patient a choice')
  })

  it('shows an escalating action in the bad tone', () => {
    renderBadge(2)
    expect(screen.getByRole('status').getAttribute('data-tone')).toBe('bad')
  })

  it('shows a repeat in the neutral tone as already credited', () => {
    renderBadge(0, true)
    const badge = screen.getByRole('status')
    expect(badge.getAttribute('data-tone')).toBe('neutral')
    expect(badge.textContent).toBe('Offer control — already credited')
  })
})
