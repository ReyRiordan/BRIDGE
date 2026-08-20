# Frontend

`web/` — the BRIDGE SPA: Vite + React 19 + TypeScript + Tailwind v4, deployed on Amplify Hosting. Self-contained npm project (its own `package.json`, lockfile and CI job); the root npm project is infra-only.

> The legacy vanilla-JS UI in `frontend/` is a separate, frozen tree that stays runnable until the final teardown.

## Structure

| Path | What it is |
|---|---|
| `web/index.html`, `src/main.tsx` | Entry point — the async bootstrap resolves the API base, configures the voice transport, then renders (load error on failure) |
| `src/App.tsx` | App shell — owns the reducer and routes on `state.phase` (start → intro → game → end) |
| `src/screens/` | The four screens: `StartScreen`, `IntroScreen`, `GameScreen`, `EndScreen` (an overlay over the frozen final scene) |
| `src/components/` | Game UI: scene stage + layers, escalation bar, timer, action badge, transcript, checklist |
| `src/state/` | `useGame` — one pure reducer over the generated `GameEvent` types, plus the derived-visual selectors and the golden-run fixtures |
| `src/types/` | The hand-written `GET /scenario` contract |
| `src/api/` | `fetchScenario()` |
| `src/index.css` | `@import 'tailwindcss'` plus the `@theme` block that IS the design system — Tailwind v4 has no config file; the `@tailwindcss/vite` plugin in `vite.config.ts` is the whole setup |
| `src/config.ts` | Runtime config: `BRIDGE_LOCAL` (from `VITE_BRIDGE_LOCAL`), `RELAY_ONLY`, and `resolveApiBaseUrl()` / `getApiBaseUrl()` (runtime `custom.apiUrl` lookup) |
| `src/vite-env.d.ts` | Vite client types + the `ImportMetaEnv` declaration for `VITE_BRIDGE_LOCAL` |
| `src/voice/` | The voice client (vendored), `useVoiceSession` (session lifecycle), the `gameEvents.ts` dispatch seam + the generated event types |
| `public/visuals/` | Scene art: `patient_{escalation}.png` + per-action active/inactive layers (a matched 1196×880 set), plus `background.jpg` and `intro.jpg` |
| `public/amplify_outputs.json` | Backend outputs (`custom.apiUrl`). **Generated, never committed** — the Hosting build writes it (see `../backend/deployment.md`); the SPA fetches it at runtime |
| `vite.config.ts` | Vite plugins, the dev-server proxy (`/voice` + `/scenario` → `localhost:8000`), and the Vitest (jsdom, globals) config |
| `eslint.config.js` | ESLint flat config: typescript-eslint, react-hooks, react-refresh, prettier |

## Doc map

| Doc | Read it for |
|---|---|
| `app-shell.md` | The phase machine, the reducer contract, the scenario-derived and no-wall-clock rules, scene-layer compositing, and the `dispatch` seam the voice client feeds |
| `voice-client.md` | `src/voice/`: the startup bootstrap, connect/reconnect/end flow, the `relayOnly` seam, and the data-channel → reducer path |
| `voice-integration.md` | How the two join: the session lifecycle (`useVoiceSession`), the two-ids model, the bounded reconnect policy, the end settle, and the connection-lost state |
| `../backend/local-dev.md` | Running the SPA against a local backend (`web/.env.local`, the proxy, what relay-only means locally) |
| `../backend/README.md` | The server side of the contract (event envelope, control-plane endpoints) |

## Generated types

`src/voice/gameEvents.gen.ts` is generated from the pydantic models in `runtime/bridge/events.py` and committed. Never hand-edit it:

```bash
python3 scripts/gen_event_types.py           # regenerate
python3 scripts/gen_event_types.py --check   # what Backend CI runs
```

It is excluded from Prettier and ESLint — the generator owns its formatting, and CI diffs the file byte for byte.

## Commands

All from `web/`:

```bash
npm ci
npm run dev            # vite dev server (against a deployed backend)
npm run build          # tsc -b && vite build
npm run lint           # eslint
npm run format:check   # prettier --check .   (npm run format writes)
npm run type-check     # tsc -b --noEmit
npm test -- --run      # vitest (what CI runs)
```

From the repo root, `npm run dev` starts this dev server *plus* the local control plane and voice runtime — see [`../backend/local-dev.md`](../backend/local-dev.md). Local mode is opt-in through `web/.env.local`:

```
VITE_BRIDGE_LOCAL=1
```

Never committed (`.env*.local` is gitignored) and never inferred from `import.meta.env.DEV` — `vite dev` against the deployed backend must keep relay-only ICE.

`.github/workflows/frontend-tests.yml` runs lint / format / type-check / test on node 24.
