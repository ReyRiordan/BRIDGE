# Frontend

`web/` — the BRIDGE SPA: Vite + React 19 + TypeScript + Tailwind v4, deployed on Amplify Hosting. Self-contained npm project (its own `package.json`, lockfile and CI job); the root npm project is infra-only.

> The legacy vanilla-JS UI in `frontend/` is a separate, frozen tree that stays runnable until the final teardown.

## Structure

| Path | What it is |
|---|---|
| `web/index.html`, `src/main.tsx` | Entry point |
| `src/App.tsx` | App shell — a placeholder until the screens land in [Rewrite F] |
| `src/index.css` | `@import 'tailwindcss'` — Tailwind v4 has no config file; the `@tailwindcss/vite` plugin in `vite.config.ts` is the whole setup |
| `src/config.ts` | Runtime flags: `BRIDGE_LOCAL` (from `VITE_BRIDGE_LOCAL`), `API_BASE_URL`, `RELAY_ONLY` |
| `src/vite-env.d.ts` | Vite client types + the `ImportMetaEnv` declaration for `VITE_BRIDGE_LOCAL` |
| `src/voice/` | The voice client (vendored) + the generated event types |
| `public/visuals/` | Layered scene art: `patient_{escalation}.png` + per-action active/inactive layers |
| `public/amplify_outputs.json` | Backend outputs (`custom.apiUrl`). **Generated, never committed** — the Hosting build writes it (see `../backend/deployment.md`); the SPA fetches it at runtime |
| `vite.config.ts` | Vite plugins, the dev-server proxy (`/voice` + `/scenario` → `localhost:8000`), and the Vitest (jsdom, globals) config |
| `eslint.config.js` | ESLint flat config: typescript-eslint, react-hooks, react-refresh, prettier |

## Doc map

| Doc | Read it for |
|---|---|
| `voice-client.md` | `src/voice/`: transport wiring, connect/reconnect flow, the `relayOnly` seam, the generated `GameEvent` types |
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
