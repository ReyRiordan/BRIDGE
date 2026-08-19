import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { resolveApiBaseUrl } from './config'
import { configureVoiceApi, type VoiceApiTransport } from './voice/voiceApi'

/**
 * Startup bootstrap.
 *
 * Nothing renders until the control-plane base URL is known: in deployed mode
 * it comes from `/amplify_outputs.json` at runtime (see ./config), and every
 * API call would otherwise fire against the wrong origin. Failure renders a
 * load error rather than an app whose requests all 404.
 */

/** Minimal fetch adapter for the voice kit — no axios dependency exists here. */
function createTransport(baseUrl: string): VoiceApiTransport {
  return {
    post: async <T,>(path: string, body?: unknown): Promise<T> => {
      const res = await fetch(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // An absent body must stay absent: the end endpoint's request model is
        // optional and `null` would fail validation.
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      })
      const payload: unknown = await res.json().catch(() => null)
      if (!res.ok) {
        // The control plane returns {detail} (with CORS) even for upstream
        // failures, so prefer it over the bare status.
        const detail = (payload as { detail?: unknown } | null)?.detail
        throw new Error(
          typeof detail === 'string'
            ? detail
            : `Request failed (${res.status})`,
        )
      }
      return payload as T
    },
  }
}

const root = createRoot(document.getElementById('root')!)

try {
  const baseUrl = await resolveApiBaseUrl()
  configureVoiceApi(createTransport(baseUrl), `${baseUrl}/voice`)
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
} catch (err) {
  console.error('Startup failed:', err)
  root.render(
    <div className="flex min-h-screen items-center justify-center p-8 text-center text-slate-200">
      <p>Could not load the simulation. Please refresh to try again.</p>
    </div>,
  )
}
