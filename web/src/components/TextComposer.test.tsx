import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import TextComposer, { MAX_TEXT_CHARS } from './TextComposer'

afterEach(cleanup)

const box = () => screen.getByLabelText('Your message') as HTMLTextAreaElement
const sendButton = () =>
  screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement
const type = (value: string) => fireEvent.change(box(), { target: { value } })
const press = (key: string, shiftKey = false) =>
  fireEvent.keyDown(box(), { key, shiftKey })

function setup(props: Partial<Parameters<typeof TextComposer>[0]> = {}) {
  const onSend = vi.fn(() => true)
  const view = render(
    <TextComposer
      onSend={onSend}
      awaitingReply={false}
      disabled={false}
      {...props}
    />,
  )
  return { onSend, ...view }
}

describe('TextComposer', () => {
  it('sends the trimmed text on Enter and clears the box', () => {
    const { onSend } = setup()
    type('  I can see you are upset.  ')
    press('Enter')

    expect(onSend).toHaveBeenCalledWith('I can see you are upset.')
    expect(box().value).toBe('')
  })

  it('sends from the button too', () => {
    const { onSend } = setup()
    type('Hello')
    act(() => sendButton().click())
    expect(onSend).toHaveBeenCalledWith('Hello')
  })

  it('does not send on Shift+Enter', () => {
    const { onSend } = setup()
    type('line one')
    press('Enter', true)

    expect(onSend).not.toHaveBeenCalled()
    expect(box().value).toBe('line one')
  })

  it('blocks empty and whitespace-only input', () => {
    const { onSend } = setup()
    expect(sendButton().disabled).toBe(true)
    press('Enter')
    type('   \n ')
    press('Enter')

    expect(sendButton().disabled).toBe(true)
    expect(onSend).not.toHaveBeenCalled()
  })

  it('is disabled while awaiting a reply, and re-enables after', () => {
    const { onSend, rerender } = setup({ awaitingReply: true })
    expect(box().disabled).toBe(true)
    expect(sendButton().disabled).toBe(true)
    press('Enter')
    expect(onSend).not.toHaveBeenCalled()

    rerender(
      <TextComposer onSend={onSend} awaitingReply={false} disabled={false} />,
    )
    expect(box().disabled).toBe(false)
    expect(document.activeElement).toBe(box())
  })

  it('is disabled once the game is over', () => {
    setup({ disabled: true })
    expect(box().disabled).toBe(true)
  })

  it('enforces the character cap and shows a counter near it', () => {
    setup()
    expect(box().maxLength).toBe(MAX_TEXT_CHARS)
    expect(screen.queryByTestId('char-counter')).toBeNull()

    type('a'.repeat(MAX_TEXT_CHARS + 50))
    expect(box().value).toHaveLength(MAX_TEXT_CHARS)
    expect(screen.getByTestId('char-counter').textContent).toBe(
      `${MAX_TEXT_CHARS}/${MAX_TEXT_CHARS}`,
    )
  })

  it('keeps the draft and says so when the send fails', () => {
    const onSend = vi.fn(() => false)
    setup({ onSend })
    type('Hello')
    press('Enter')

    expect(box().value).toBe('Hello')
    expect(screen.getByText(/Not sent/)).toBeDefined()
  })
})
