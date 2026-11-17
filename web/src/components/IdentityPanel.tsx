import { useId, useState, type FormEvent } from 'react'
import Button from './Button'
import { normalizeNetId, type Identity, type Mode } from '../identity'

export const RECORDING_NOTICE =
  'Your NetID, your typed or transcribed responses, and your performance in each case are saved and reviewed by the course/research team. Audio is not recorded or stored.'

const MODES: { value: Mode; label: string }[] = [
  { value: 'voice', label: 'Voice' },
  { value: 'text', label: 'Text' },
]

interface IdentityPanelProps {
  identity: Identity | null
  onChange: (identity: Identity | null) => void
}

/**
 * Sign-in for the Start screen: a self-reported NetID, the Voice/Text choice
 * and the recording notice. Once confirmed it collapses to a one-line summary
 * with a "Change" link (for shared lab machines).
 */
function IdentityPanel({ identity, onChange }: IdentityPanelProps) {
  const [draft, setDraft] = useState(identity?.netid ?? '')
  const [mode, setMode] = useState<Mode>(identity?.mode ?? 'voice')
  const [submitted, setSubmitted] = useState(false)
  const inputId = useId()
  const errorId = useId()

  const netid = normalizeNetId(draft)
  const showError = submitted && netid === null

  function confirm(event: FormEvent) {
    event.preventDefault()
    setSubmitted(true)
    if (netid !== null) onChange({ netid, mode })
  }

  if (identity !== null) {
    return (
      <section
        aria-label="Signed in"
        className="flex w-full max-w-xl flex-col items-center gap-2 text-center"
      >
        <p className="text-sm text-ink-muted">
          Signed in as <strong className="text-ink">{identity.netid}</strong>
          {' · '}
          {identity.mode === 'voice' ? 'Voice' : 'Text'}
          {' · '}
          Not you?{' '}
          <button
            type="button"
            onClick={() => {
              setDraft('')
              setSubmitted(false)
              onChange(null)
            }}
            className="cursor-pointer text-accent underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none"
          >
            Change
          </button>
        </p>
        <p className="text-xs leading-relaxed text-ink-muted">
          {RECORDING_NOTICE}
        </p>
      </section>
    )
  }

  return (
    <form
      aria-label="Sign in"
      onSubmit={confirm}
      noValidate
      className="flex w-full max-w-xl flex-col gap-4 rounded-card border border-edge bg-surface p-5"
    >
      <div className="flex flex-col gap-2">
        <label
          htmlFor={inputId}
          className="text-xs font-semibold tracking-[0.24em] text-ink-muted uppercase"
        >
          Rutgers NetID
        </label>
        <input
          id={inputId}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          placeholder="abc123"
          aria-invalid={showError}
          aria-describedby={showError ? errorId : undefined}
          className="rounded-lg border border-edge bg-surface-raised px-3 py-2 text-ink placeholder:text-ink-muted/60 focus-visible:border-accent focus-visible:outline-none aria-invalid:border-bad"
        />
        {showError && (
          <p id={errorId} className="text-xs text-bad">
            Enter your NetID: letters, optionally followed by digits (e.g.
            abc123).
          </p>
        )}
        <p className="text-xs leading-relaxed text-ink-muted">
          {RECORDING_NOTICE}
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          role="radiogroup"
          aria-label="Response mode"
          className="inline-flex rounded-full border border-edge bg-surface-raised p-1"
        >
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={mode === m.value}
              onClick={() => setMode(m.value)}
              className={`cursor-pointer rounded-full px-4 py-1.5 text-xs font-semibold tracking-wide uppercase transition focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none ${
                mode === m.value
                  ? 'bg-accent-strong text-ink'
                  : 'text-ink-muted hover:text-ink'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <Button type="submit" className="px-6 py-2">
          Continue
        </Button>
      </div>
    </form>
  )
}

export default IdentityPanel
