import { useState, type FormEvent } from 'react'
import Button from '../components/Button'
import { login } from './api'

interface LoginFormProps {
  onToken: (token: string) => void
  /** Why the form is showing again, e.g. an expired token. */
  notice?: string | null
}

/** The shared-password gate. The server checks the password, not this form. */
function LoginForm({ onToken, notice }: LoginFormProps) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError(null)
    try {
      onToken(await login(password))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Sign-in failed.')
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <form
        onSubmit={(e) => void submit(e)}
        className="flex w-full max-w-sm flex-col gap-5 rounded-card border border-edge bg-surface p-6"
      >
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-[0.24em] text-ink">
            BRIDGE
          </h1>
          <p className="text-xs font-semibold tracking-[0.24em] text-accent uppercase">
            Results
          </p>
        </div>
        {notice && <p className="text-sm text-ink-muted">{notice}</p>}
        <label className="flex flex-col gap-1.5 text-sm text-ink-muted">
          Password
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={error !== null}
            className="rounded-lg border border-edge bg-surface-raised px-3 py-2.5 text-ink focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-bad">
            {error}
          </p>
        )}
        <Button type="submit" disabled={!password || busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </main>
  )
}

export default LoginForm
