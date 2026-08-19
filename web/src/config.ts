/**
 * Runtime configuration for the SPA.
 *
 * Local mode is EXPLICIT, never inferred from `import.meta.env.DEV`: running
 * `vite dev` against the deployed backend is a normal workflow, and it must
 * keep relay-only ICE. Inferring from DEV would hand that session host
 * candidates, which connect locally and hide the TURN-only reality the cloud
 * enforces — the exact trap this flag exists to avoid.
 *
 * Set `VITE_BRIDGE_LOCAL=1` in `web/.env.local` (gitignored) to opt in; see
 * ../../docs/backend/local-dev.md.
 */

/** True only when web/.env.local opts this build into the local dev stack. */
export const BRIDGE_LOCAL = import.meta.env.VITE_BRIDGE_LOCAL === '1'

/**
 * Base URL for control-plane calls, resolved once at startup.
 *
 * Local mode is same-origin: the Vite dev server proxies /voice and /scenario
 * to the API on :8000, so the base stays empty and no CORS is involved. In
 * every deployed mode the Lambda Function URL is a different origin and is
 * only known after the backend deploys, so it is resolved at RUNTIME from the
 * `amplify_outputs.json` the Hosting build copies into web/public/ — a
 * build-time VITE_API_URL would couple the SPA build to deploy ordering.
 */
let apiBaseUrl = ''

/** The resolved base. Empty until `resolveApiBaseUrl()` has run (and in local mode). */
export const getApiBaseUrl = (): string => apiBaseUrl

/**
 * Resolve and memoize the control-plane base URL. Called once from the
 * `main.tsx` bootstrap before anything renders or talks to the API.
 *
 * @throws if the outputs file is unreachable or carries no `custom.apiUrl` —
 *   the bootstrap surfaces that as a load error rather than rendering an app
 *   whose every request would fail.
 */
export async function resolveApiBaseUrl(): Promise<string> {
  if (BRIDGE_LOCAL) {
    apiBaseUrl = ''
    return apiBaseUrl
  }

  const res = await fetch('/amplify_outputs.json')
  if (!res.ok) {
    throw new Error(`Failed to load amplify_outputs.json (HTTP ${res.status})`)
  }
  const body: unknown = await res.json()
  const url = (body as { custom?: { apiUrl?: unknown } })?.custom?.apiUrl
  if (typeof url !== 'string' || url === '') {
    throw new Error('amplify_outputs.json has no custom.apiUrl')
  }
  apiBaseUrl = url.replace(/\/+$/, '')
  return apiBaseUrl
}

/**
 * Whether the browser pins `iceTransportPolicy: 'relay'`. True everywhere
 * except local mode, where both peers are on loopback and there is no TURN.
 */
export const RELAY_ONLY = !BRIDGE_LOCAL
