# BRIDGE — Behavioral Response and Interactive De-escalation Guided Education

## What This Is

A voice-to-voice medical training simulation where medical students practice behavioral de-escalation with an AI patient. The student speaks aloud; their words are transcribed, analyzed for de-escalation actions, and used to drive a patient AI response. An escalation bar tracks patient agitation. The goal is to reduce it to zero before time runs out.

## Architecture

- **`web/`** — Vite + React + TS + Tailwind SPA on Amplify Hosting.
- **`api/`** — thin FastAPI control-plane Lambda (`/scenario` + voice signaling), container image under the Lambda Web Adapter.
- **`runtime/`** — the voice pipeline on Bedrock AgentCore + pipecat, built on the vendored `voice_kit` package; `runtime/bridge/` holds the game engine.
- **`amplify/`** — Amplify Gen 2 TS CDK infra.
- **`resources/`** — scenario config + prompts, COPYed into both container images.

Every change follows: **implement → write tests → update docs → tests/lint green in CI** (`.github/workflows/`).

## Commands

Use 'python3' to run any python files. Use the GitHub CLI ('gh') for all GitHub-related tasks.

### Local dev

The whole stack on one machine, zero AWS calls — SPA :5173, control plane :8000, voice runtime :8080:

```bash
# One-time: repo-root venv (Python 3.11) + web/.env.local with VITE_BRIDGE_LOCAL=1
python3 -m venv .venv
.venv/bin/pip install -r api/requirements.txt -r runtime/requirements-voice.txt

npm run dev                                    # all three processes
.venv/bin/python scripts/local_voice_smoke.py  # Tier-1 WebRTC smoke
```

`BRIDGE_LOCAL=1` is the umbrella flag; it requires the non-AWS providers (`together`/`inworld`/`openrouter`) and refuses to run under `ENV=production`. Full setup and the local/cloud ICE divergence: [`docs/backend/local-dev.md`](docs/backend/local-dev.md) — **never merge on local-only verification.**

## Exploration Workflow

When working on or exploring the codebase from a fresh start, ALWAYS start by reading the README of the relevant layer's documentation folder (frontend/, backend/). Then, use the documentation map in this README to navigate to the specific docs relevant to the current task. These docs will provide you with the core context and point to specific code files that you can read as necessary. Always do this relevant doc reading BEFORE you do actual codebase exploration.

This workflow is MANDATORY as it ensures a clear top-down understanding of codebase, maximizes exploration efficiency, and minimizes unnecessary code file reads.

You MUST keep the docs up to date at all times because they are such a core part of this workflow - do not end a session where you made changes without updating the relevant docs as needed. The docs should only concisely reflect the current state, not how we got there (do not reference any specific issues/PRs).

**Documentation layers** (context path: `CLAUDE.md → layer README → specific docs → code`):

| Layer | Doc folder | Covers |
|---|---|---|
| frontend | `docs/frontend/` | `web/` (SPA screens, game UI, voice client) |
| backend | `docs/backend/` | `api/` (control-plane Lambda), `runtime/` (AgentCore voice pipeline + game engine), `amplify/` (infra). Includes `docs/backend/voice-kit/` ops docs (architecture, configuration, infrastructure, deploy runbook, gotchas). |

Start at [`docs/frontend/README.md`](docs/frontend/README.md) or [`docs/backend/README.md`](docs/backend/README.md) — each carries the doc map for its layer.
