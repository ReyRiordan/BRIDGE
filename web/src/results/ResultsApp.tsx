import { useCallback, useEffect, useState } from 'react'
import { fetchScenario } from '../api/scenario'
import type { Scenario } from '../types/scenario'
import LoginForm from './LoginForm'
import SessionDetail from './SessionDetail'
import SessionList from './SessionList'
import {
  Unauthorized,
  clearToken,
  getSession,
  listSessions,
  loadToken,
  saveToken,
} from './api'
import { EMPTY_FILTERS, type ListFilters } from './filters'
import { LIST_HASH, detailHash, parseResultsRoute, useHash } from './route'
import type { SessionRecord, SessionSummary } from './types'

const EXPIRED = 'Your sign-in has expired. Enter the password again.'

function message(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback
}

/**
 * The collaborators' results page (`#/results`), unlinked from the student
 * UI. Gated on a server-issued token held in `sessionStorage`; any 401 drops
 * it and shows the login form again. The summary list is fetched once per
 * sign-in and kept, with its filters, across list ↔ detail navigation.
 */
function ResultsApp() {
  const route = parseResultsRoute(useHash())
  const [token, setToken] = useState<string | null>(() => loadToken())
  const [notice, setNotice] = useState<string | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [filters, setFilters] = useState<ListFilters>(EMPTY_FILTERS)
  const [reload, setReload] = useState(0)

  const signOut = useCallback((why: string | null) => {
    clearToken()
    setToken(null)
    setSessions(null)
    setNotice(why)
  }, [])

  const onFailure = useCallback(
    (cause: unknown, fallback: string): string | null => {
      if (cause instanceof Unauthorized) {
        signOut(EXPIRED)
        return null
      }
      return message(cause, fallback)
    },
    [signOut],
  )

  useEffect(() => {
    if (token === null) return
    let live = true
    listSessions(token).then(
      (list) => {
        if (!live) return
        setSessions(list)
        setListError(null)
      },
      (cause: unknown) => {
        if (live) setListError(onFailure(cause, 'Could not load sessions.'))
      },
    )
    return () => {
      live = false
    }
  }, [token, reload, onFailure])

  if (token === null) {
    return (
      <LoginForm
        notice={notice}
        onToken={(t) => {
          saveToken(t)
          setNotice(null)
          setToken(t)
        }}
      />
    )
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center gap-4">
        <a
          href={LIST_HASH}
          className="flex items-baseline gap-3 focus-visible:outline-none"
        >
          <span className="text-xl font-semibold tracking-[0.24em] text-ink">
            BRIDGE
          </span>
          <span className="text-xs font-semibold tracking-[0.24em] text-accent uppercase">
            Results
          </span>
        </a>
        <nav className="ml-auto flex items-center gap-4 text-xs text-ink-muted">
          {route.view === 'detail' ? (
            <a
              href={LIST_HASH}
              className="hover:text-ink focus-visible:text-ink"
            >
              ← All sessions
            </a>
          ) : (
            <button
              type="button"
              onClick={() => setReload((n) => n + 1)}
              className="cursor-pointer hover:text-ink focus-visible:text-ink focus-visible:outline-none"
            >
              Refresh
            </button>
          )}
          <button
            type="button"
            onClick={() => signOut(null)}
            className="cursor-pointer hover:text-ink focus-visible:text-ink focus-visible:outline-none"
          >
            Sign out
          </button>
        </nav>
      </header>

      {route.view === 'detail' ? (
        <DetailRoute
          key={route.id}
          token={token}
          id={route.id}
          onFailure={onFailure}
        />
      ) : listError ? (
        <p role="alert" className="text-sm text-bad">
          {listError}
        </p>
      ) : sessions === null ? (
        <p className="text-sm text-ink-muted">Loading sessions…</p>
      ) : (
        <SessionList
          sessions={sessions}
          filters={filters}
          onFiltersChange={setFilters}
          onOpen={(id) => {
            window.location.hash = detailHash(id)
          }}
        />
      )}
    </div>
  )
}

function DetailRoute({
  token,
  id,
  onFailure,
}: {
  token: string
  id: string
  onFailure: (cause: unknown, fallback: string) => string | null
}) {
  const [record, setRecord] = useState<SessionRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [scenarioError, setScenarioError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getSession(token, id).then(
      (item) => {
        if (!live) return
        setRecord(item)
        fetchScenario(item.scenario_id).then(
          (s) => {
            if (live) setScenario(s)
          },
          (cause: unknown) => {
            if (live) {
              setScenarioError(
                `The checklist is unavailable: ${message(cause, 'the scenario did not load')}.`,
              )
            }
          },
        )
      },
      (cause: unknown) => {
        if (live) setError(onFailure(cause, 'Could not load the session.'))
      },
    )
    return () => {
      live = false
    }
  }, [token, id, onFailure])

  if (error) {
    return (
      <p role="alert" className="text-sm text-bad">
        {error}
      </p>
    )
  }
  if (record === null) {
    return <p className="text-sm text-ink-muted">Loading the session…</p>
  }
  return (
    <SessionDetail
      record={record}
      scenario={scenario}
      scenarioError={scenarioError}
    />
  )
}

export default ResultsApp
