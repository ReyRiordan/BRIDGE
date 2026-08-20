/**
 * The fetch adapter the voice kit's control-plane calls run through.
 *
 * The control-plane base URL is applied HERE and nowhere else: the kit's
 * `configureVoiceApi(transport, basePath)` also accepts an absolute basePath,
 * and passing one on top of a base-carrying transport doubles the origin into
 * `https://host…https//host…/voice/...` — a host that does not resolve, so the
 * fetch fails at the network layer with no request ever reaching the API. It
 * is invisible in local mode, where the base is '' and the two spellings
 * coincide, so the invariant is pinned by tests instead.
 */

import type { VoiceApiTransport } from '../voice/voiceApi'

/** Minimal fetch adapter for the voice kit — no axios dependency exists here. */
export function createTransport(baseUrl: string): VoiceApiTransport {
  return {
    post: async <T>(path: string, body?: unknown): Promise<T> => {
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
