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
 * Base URL for control-plane calls. Empty means same-origin: locally the Vite
 * dev server proxies /voice and /scenario to the API on :8000, so no CORS is
 * involved on the local path. [Rewrite G] extends this with the deployed
 * `amplify_outputs.json` fetch.
 */
export const API_BASE_URL = ''

/**
 * Whether the browser pins `iceTransportPolicy: 'relay'`. True everywhere
 * except local mode, where both peers are on loopback and there is no TURN.
 */
export const RELAY_ONLY = !BRIDGE_LOCAL
