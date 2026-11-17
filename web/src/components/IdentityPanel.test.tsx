import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import IdentityPanel, { RECORDING_NOTICE } from './IdentityPanel'

afterEach(cleanup)

const input = () => screen.getByLabelText('Rutgers NetID') as HTMLInputElement
const type = (value: string) => fireEvent.change(input(), { target: { value } })
const submit = () =>
  act(() => {
    screen.getByRole('button', { name: 'Continue' }).click()
  })

describe('IdentityPanel', () => {
  it('shows the recording notice verbatim', () => {
    render(<IdentityPanel identity={null} onChange={() => {}} />)
    expect(screen.getByText(RECORDING_NOTICE)).toBeDefined()
    expect(RECORDING_NOTICE).toBe(
      'Your NetID, your typed or transcribed responses, and your performance in each case are saved and reviewed by the course/research team. Audio is not recorded or stored.',
    )
  })

  it('blocks an invalid NetID with an inline error', async () => {
    const onChange = vi.fn()
    render(<IdentityPanel identity={null} onChange={onChange} />)
    type('ab-12')
    await submit()
    expect(onChange).not.toHaveBeenCalled()
    expect(input().getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByText(/Enter your NetID/)).toBeDefined()
  })

  it('strips an email suffix and confirms with the chosen mode', async () => {
    const onChange = vi.fn()
    render(<IdentityPanel identity={null} onChange={onChange} />)
    type(' ABC123@rutgers.edu ')
    await act(() => {
      screen.getByRole('radio', { name: 'Text' }).click()
    })
    await submit()
    expect(onChange).toHaveBeenCalledWith({ netid: 'abc123', mode: 'text' })
  })

  it('defaults to voice', async () => {
    const onChange = vi.fn()
    render(<IdentityPanel identity={null} onChange={onChange} />)
    expect(
      screen.getByRole('radio', { name: 'Voice' }).getAttribute('aria-checked'),
    ).toBe('true')
    type('jd12')
    await submit()
    expect(onChange).toHaveBeenCalledWith({ netid: 'jd12', mode: 'voice' })
  })

  it('collapses once signed in, and Change clears the identity', async () => {
    const onChange = vi.fn()
    render(
      <IdentityPanel
        identity={{ netid: 'abc123', mode: 'voice' }}
        onChange={onChange}
      />,
    )
    const summary = screen.getByRole('region', { name: 'Signed in' })
    expect(summary.textContent).toContain('Signed in as abc123 · Voice')
    expect(screen.getByText(RECORDING_NOTICE)).toBeDefined()
    await act(() => {
      screen.getByRole('button', { name: 'Change' }).click()
    })
    expect(onChange).toHaveBeenCalledWith(null)
  })
})
