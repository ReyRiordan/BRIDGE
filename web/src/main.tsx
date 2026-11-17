import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import ResultsApp from './results/ResultsApp'
import { isResultsHash, useHash } from './results/route'
import { resolveApiBaseUrl } from './config'
import { createTransport } from './api/transport'
import { configureVoiceApi } from './voice/voiceApi'

/**
 * Startup bootstrap.
 *
 * Nothing renders until the control-plane base URL is known: in deployed mode
 * it comes from `/amplify_outputs.json` at runtime (see ./config), and every
 * API call would otherwise fire against the wrong origin. Failure renders a
 * load error rather than an app whose requests all 404.
 */

/**
 * The collaborators' results page lives on the same site at `#/results`
 * (unlinked from the student UI); everything else is the simulation.
 */
// eslint-disable-next-line react-refresh/only-export-components
function Root() {
  return isResultsHash(useHash()) ? <ResultsApp /> : <App />
}

const root = createRoot(document.getElementById('root')!)

try {
  const baseUrl = await resolveApiBaseUrl()
  // The base URL lives in the transport; basePath stays relative (api/transport.ts).
  configureVoiceApi(createTransport(baseUrl))
  root.render(
    <StrictMode>
      <Root />
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
