# BRIDGE — Behavioral Response and Interactive De-escalation Guided Education

A voice-to-voice medical training simulation. A student speaks aloud with an AI
patient in real time; each utterance is transcribed, scored for de-escalation
actions, and used to drive the patient's reply. An escalation bar tracks the
patient's agitation, and the scene reacts as actions land.

**Win:** bring escalation from 5 down to 0 before the 5-minute timer expires.
**Lose:** escalation reaches 10, or time runs out.

## Architecture at a glance

```
Browser (SPA)  ──HTTPS──▶  Control plane (Lambda)  ──invoke──▶  Voice runtime (AgentCore)
     │                     /scenario + WebRTC signaling                    │
     └──────────────────── WebRTC audio + data channel ─────────────────────┘
```

| Tree | What it is |
|---|---|
| `web/` | Vite + React + TypeScript + Tailwind SPA, deployed on Amplify Hosting. Own package.json and CI job. |
| `api/` | Thin FastAPI control plane — `GET /scenario` plus the voice signaling router. Container image on Lambda under the Lambda Web Adapter. Deliberately pipecat-free. |
| `runtime/` | The voice pipeline: `voice_kit/` (vendored pipecat kit — STT, LLM, TTS, WebRTC transport) and `bridge/` (the game engine: referee, session state, events, timer). Runs on Bedrock AgentCore. |
| `amplify/` | Amplify Gen 2 / CDK infra that provisions the Lambda, its Function URL, and the voice runtime. |
| `resources/` | Scenario config and prompts, COPYed into both container images. |
| `scripts/` | Event-contract codegen and the local WebRTC smoke test. |

One turn, in order: STT → referee (scores the utterance, applies point values,
emits events) → patient LLM → TTS. Game events reach the SPA over the WebRTC
data channel.

## Local quickstart

Runs the whole stack on one machine with zero AWS calls — SPA on :5173, control
plane on :8000, voice runtime on :8080. Needs Python 3.11 and Node 20+.

```bash
# One-time
python3 -m venv .venv
.venv/bin/pip install -r api/requirements.txt -r runtime/requirements-voice.txt
npm install && npm --prefix web install
cp .env.example .env                      # fill in the provider API keys
echo 'VITE_BRIDGE_LOCAL=1' > web/.env.local

npm run dev                                    # all three processes
.venv/bin/python scripts/local_voice_smoke.py  # WebRTC smoke test
```

Local mode requires the non-AWS providers — set `STT_PROVIDER=together`,
`TTS_PROVIDER=inworld` and `LLM_PROVIDER=openrouter` in `.env` — and refuses to
start under `ENV=production`. Full setup, plus where local diverges from the
cloud, is in [`docs/backend/local-dev.md`](docs/backend/local-dev.md). **Never
merge on local-only verification.**

## Docs

| Layer | Start here |
|---|---|
| Frontend | [`docs/frontend/README.md`](docs/frontend/README.md) — SPA screens, game UI, voice client |
| Backend | [`docs/backend/README.md`](docs/backend/README.md) — control plane, voice runtime, game engine, infra, deploy runbook |

Each README carries the doc map for its layer.
