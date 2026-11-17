import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import Button from './Button'

/** The runtime drops anything longer (`MAX_STUDENT_TEXT_CHARS`). */
export const MAX_TEXT_CHARS = 1000
/** The counter appears once this close to the cap. */
const COUNTER_FROM = MAX_TEXT_CHARS - 100

interface TextComposerProps {
  /** Send one typed turn. False means it was not sent (the channel is down). */
  onSend: (content: string) => boolean
  /** A message is in flight: nothing more is sent until the patient replies. */
  awaitingReply: boolean
  /** The game is over, or there is no connection. */
  disabled: boolean
}

/**
 * Text mode's input: the student types what they would have said.
 *
 * Enter sends, Shift+Enter adds a newline. One message is in flight at a time,
 * so the whole composer is disabled while `awaitingReply`, and focus returns
 * to the box when it re-enables.
 */
function TextComposer({ onSend, awaitingReply, disabled }: TextComposerProps) {
  const [draft, setDraft] = useState('')
  const [failed, setFailed] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const counterId = useId()
  const locked = disabled || awaitingReply
  const content = draft.trim()
  const canSend = !locked && content.length > 0

  // Back to the box once the patient has answered, ready for the next line.
  useEffect(() => {
    if (!locked) inputRef.current?.focus()
  }, [locked])

  const send = () => {
    if (!canSend) return
    if (onSend(content)) {
      setDraft('')
      setFailed(false)
    } else {
      setFailed(true)
    }
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    send()
  }

  return (
    <form
      onSubmit={onSubmit}
      aria-label="Message the patient"
      className="flex flex-col gap-2 rounded-card border border-edge bg-surface p-3"
    >
      <textarea
        ref={inputRef}
        aria-label="Your message"
        aria-describedby={counterId}
        rows={3}
        maxLength={MAX_TEXT_CHARS}
        value={draft}
        disabled={locked}
        placeholder={
          awaitingReply
            ? 'Waiting for the patient…'
            : 'Type what you would say…'
        }
        onChange={(event) =>
          setDraft(event.target.value.slice(0, MAX_TEXT_CHARS))
        }
        onKeyDown={(event) => {
          if (
            event.key === 'Enter' &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault()
            send()
          }
        }}
        className="resize-none rounded-lg border border-edge bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted/60 focus-visible:border-accent focus-visible:outline-none disabled:opacity-60"
      />
      <div className="flex items-center justify-between gap-3">
        <p id={counterId} className="text-xs text-ink-muted">
          {failed ? (
            <span className="text-bad">Not sent — try again.</span>
          ) : draft.length >= COUNTER_FROM ? (
            <span data-testid="char-counter">
              {draft.length}/{MAX_TEXT_CHARS}
            </span>
          ) : (
            'Enter to send · Shift+Enter for a new line'
          )}
        </p>
        <Button type="submit" disabled={!canSend} className="px-5 py-2">
          Send
        </Button>
      </div>
    </form>
  )
}

export default TextComposer
