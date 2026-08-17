# BRIDGE — Behavioral Response and Interactive De-escalation Guided Education

## What This Is

A voice-to-voice medical training simulation where medical students practice behavioral de-escalation with an AI patient. The student speaks aloud; their words are transcribed, analyzed for de-escalation actions, and used to drive a patient AI response. An escalation bar tracks patient agitation. The goal is to reduce it to zero before time runs out.

> Formerly known as **MEWAI**. Renamed to BRIDGE as part of the ongoing rewrite; legacy references to MEWAI may persist in older files until final teardown.

## ⚠️ Rewrite In Progress

The repo is mid-rewrite from a single-process prototype (FastAPI + FastRTC + Gradio) to a production app on AWS:

- **New architecture:** Vite + React + TS + Tailwind SPA (`web/`) on Amplify Hosting; a thin FastAPI control-plane Lambda (`api/`, via Mangum); the voice pipeline on **Bedrock AgentCore + pipecat** (`runtime/`, built from the vendored `voice-pipeline-kit/`); Amplify Gen 2 TS CDK infra (`amplify/`).
- The rewrite is **additive**: the legacy app stays runnable until the final teardown issue. Old and new trees coexist (`frontend/` = legacy, `web/` = new).
- The rewrite is tracked as GitHub issues `[Rewrite A]`–`[Rewrite I]`; see the tracking issue for wave ordering.
- Every rewrite change follows: **implement → write tests → update docs → tests/lint green in CI** (`.github/workflows/`).

## Exploration Workflow

When working on or exploring the codebase from a fresh start, ALWAYS start by reading the README of the relevant layer's documentation folder (frontend/, backend/). Then, use the documentation map in this README to navigate to the specific docs relevant to the current task. These docs will provide you with the core context and point to specific code files that you can read as necessary. Always do this relevant doc reading BEFORE you do actual codebase exploration.

This workflow is MANDATORY as it ensures a clear top-down understanding of codebase, maximizes exploration efficiency, and minimizes unnecessary code file reads.

You MUST keep the docs up to date at all times because they are such a core part of this workflow - do not end a session where you made changes without updating the relevant docs as needed. The docs should only concisely reflect the current state, not how we got there (do not reference any specific issues/PRs).

**Documentation layers** (context path: `CLAUDE.md → layer README → specific docs → code`):

| Layer | Doc folder | Covers |
|---|---|---|
| frontend | `docs/frontend/` | `web/` (SPA screens, game UI, voice client) |
| backend | `docs/backend/` | `api/` (control-plane Lambda), `runtime/` (AgentCore voice pipeline + game engine), `amplify/` (infra). Includes `docs/backend/voice-kit/` ops docs (architecture, configuration, infrastructure, deploy runbook, gotchas). |

Until the doc folders are scaffolded (rewrite issue A), the kit's docs live at `voice-pipeline-kit/docs/` and the legacy app is documented in the section below.

## Legacy App (still runnable during the rewrite)

Stack: FastAPI + FastRTC + Gradio, deployed as a single process.

```
pip install -r requirements.txt
# Add API keys to .env (see below)
python3 app.py
# Visit http://localhost:7860
```

### Required .env keys
```
OPENROUTER_API_KEY   # Claude Haiku via OpenRouter (system + patient agents)
TOGETHER_API_KEY     # Parakeet STT via Together AI
INWORLD_API_KEY      # Inworld TTS (streaming audio)
```

### Optional .env keys (per-agent model + reasoning effort)
Each defaults to `anthropic/claude-haiku-4.5` / effort `none` if unset.
Effort values: `none | low | medium | high` (passed to OpenRouter's `reasoning.effort`).
```
SYSTEM_AGENT_MODEL / SYSTEM_AGENT_EFFORT     # system (referee) agent
PATIENT_AGENT_MODEL / PATIENT_AGENT_EFFORT   # patient agent
```

### Legacy file map

| File | Responsibility |
|------|---------------|
| `app.py` | Entry point. Loads env/resources, instantiates AI clients, wires modules, starts uvicorn. |
| `backend/agents.py` | AI wrappers: `ParakeetSTT`, `OpenRouterChat`, `InworldTTS`. No game logic. |
| `backend/game.py` | `GameState` dataclass + module singletons; `load_scenario()`, `load_patient_prompt()`. |
| `backend/handlers.py` | Per-turn pipeline: STT → system agent → apply actions → check terminal → patient agent → TTS. |
| `backend/routes.py` | FastAPI endpoints (`/`, `/scenario`, `/ws`), WebSocket broadcast, timer, `reset_game()`. |
| `frontend/` | Legacy vanilla-JS UI (4 screens, WebSocket client, layered scene compositing). |
| `resources/scenario_1.json` | Scenario config (actions with `point_change`/`persist`/`layer`/`active`/`inactive` visuals, point bar, time limit, TTS settings). **Shared with the new app.** |
| `resources/patient.txt` / `patient.json` | Patient agent system prompt + case file. |
| `resources/system.txt` | System (referee) agent prompt. |
| `visuals/*.png` | Layered scene art: `patient_{escalation}.png` + per-action active/inactive layers composited by the frontend. |

### Legacy key mechanics

- **Escalation** (`0–10`) is the central mechanic: drives patient response style, locked-info reveal (only at 0), win/loss (0 → success, 10 or timeout → fail), and which visual layers render.
- Positive `point_change` = escalating action; negative = de-escalating. The system agent returns `type` strings matching `actions` in the scenario JSON.
- WebSocket `/ws` pushes `state_update`, `action_detected`, `timer`, `game_over`, `transcript_update`; client sends `begin` / `reset`.
- Audio I/O runs inside a Gradio iframe mounted at `/gradio` (removed in the rewrite).
