import { useSyncExternalStore } from 'react'

/**
 * Hash routing for the results page: `#/results` is the list and
 * `#/results/<session_id>` one session. A hash route needs no hosting rewrite
 * rule (a path route would 404 on a direct load), and two screens do not earn
 * a router library.
 */

export type ResultsRoute = { view: 'list' } | { view: 'detail'; id: string }

const PREFIX = '#/results'

export function isResultsHash(hash: string): boolean {
  return hash === PREFIX || hash.startsWith(`${PREFIX}/`)
}

export function parseResultsRoute(hash: string): ResultsRoute {
  const rest = hash.slice(PREFIX.length + 1)
  if (!isResultsHash(hash) || !rest) return { view: 'list' }
  try {
    return { view: 'detail', id: decodeURIComponent(rest) }
  } catch {
    return { view: 'list' }
  }
}

export function detailHash(id: string): string {
  return `${PREFIX}/${encodeURIComponent(id)}`
}

export const LIST_HASH = PREFIX

function subscribe(onChange: () => void) {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

/** The current `location.hash`, re-rendering on every `hashchange`. */
export function useHash(): string {
  return useSyncExternalStore(subscribe, () => window.location.hash)
}
