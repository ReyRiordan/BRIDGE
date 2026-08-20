# Local dev mode

`BRIDGE_LOCAL=1` runs the whole stack on one machine — SPA :5173, control plane :8000, voice runtime :8080 — with **zero AWS calls**. Game logic, prompts and UI then iterate in seconds instead of an ARM64 container deploy.

> **Never merge on local-only verification.** Local mode connects on loopback *host* candidates; the cloud connects over KVS-managed TURN only. A change can work perfectly here and stall behind TURN in the deployed stack. The relay-only post-deploy check in [`voice-kit/04-deploy-runbook.md`](voice-kit/04-deploy-runbook.md) stays a mandatory gate.

## One-time setup

Python **3.11** (`.python-version`), and a repo-root `.venv` — the dev scripts expect `.venv/bin/python` and fail fast with this exact hint if it is missing:

```bash
python3 -m venv .venv
.venv/bin/pip install -r api/requirements.txt -r runtime/requirements-voice.txt
```

`requirements-voice.txt` is pipecat + aiortc + opencv + onnxruntime (`numpy<2`): expect a multi-minute, ~1 GB install. It is the likeliest first-run failure — if `cv2` or a numpy wheel fails, check the Python version first.

Then the two env files:

**`.env`** (repo root, gitignored) — the provider keys plus the local provider trio. Local mode **refuses** the AWS-backed providers, so these three must be set:

```
STT_PROVIDER=together
TTS_PROVIDER=inworld
LLM_PROVIDER=openrouter
# REFEREE_PROVIDER defaults to openrouter; `bedrock` is refused too.
TOGETHER_API_KEY=...
INWORLD_API_KEY=...
OPENROUTER_API_KEY=...
```

These four deliberately differ from the deploy, which runs Transcribe for STT and Bedrock for both LLM agents (`amplify/constants.ts`). Copying the deployed values into `.env` makes local mode refuse to start, which is the intended outcome, not a bug.

Leave `SCENARIO_PATH` / `REFEREE_PROMPT_PATH` / `PATIENT_*_PATH` **unset**: they default to the repo's own `resources/` files, and the deployed `/app/resources/...` values crash a local run.

**`web/.env.local`** (gitignored via `.env*.local`):

```
VITE_BRIDGE_LOCAL=1
```

This is deliberately explicit rather than `import.meta.env.DEV` — running `vite dev` against the *deployed* backend is a normal workflow and must keep relay-only ICE.

## Running it

```bash
npm run dev     # concurrently: web (5173) + api (8000) + rt (8080)
```

| Script | What it starts |
|---|---|
| `dev:web` | `npm --prefix web run dev` — Vite, with a `server.proxy` sending `/voice` and `/scenario` to :8000 (same-origin, so no CORS on the local path) |
| `dev:api` | `uvicorn api.local:app` on :8000 — `api/local.py` is `api/main.py` with `BRIDGE_LOCAL` set *before* the import |
| `dev:runtime` | `uvicorn bridge.app:app` on :8080 — the same AgentCore app the container runs, serving `/ping` + `/invocations` |
| `dev:venv` | The `.venv` guard both backend scripts run first |

Three details in those commands are load-bearing:

- **`PYTHONPATH=runtime` on both Python processes.** `api/requirements.txt` installs `./runtime` non-editably, so without this the API imports the *installed* `voice_kit` and your edits are invisible until you reinstall.
- **`--reload` means exactly one worker.** Never add `--workers > 1`: peer-connection state and the game-session registry are process-local (gotcha #7).
- **`--reload-dir resources --reload-include "*.txt" --reload-include "*.json"`.** `bridge/config.py`'s loaders are `lru_cache`d per process, so a prompt edit only takes effect on restart — the restart *is* the "iterate on prompts in seconds" payoff. Touch `resources/referee.txt` and watch the rt pane reload.

## What the flag actually changes

`BRIDGE_LOCAL` is one umbrella flag on `VoiceKitSettings` (`runtime/voice_kit/config.py`). It gates the three ICE divergences and nothing else:

| Divergence | Deployed | `BRIDGE_LOCAL=1` |
|---|---|---|
| KVS ICE fetch in `/start` (`control_plane/router.py`) | `fetch_ice_servers()` | skipped; `ice_servers: []` |
| KVS ICE fetch in the runtime (`voice_kit/runtime.py`) | `build_ice_servers(fetch_ice_servers())` | `[]` (not `None` — aiortc substitutes a public STUN server for `None`) |
| Relay-only SDP filter | `filter_relay_only_sdp(answer)` | answer returned unfiltered |
| Browser `iceTransportPolicy` | `'relay'` | `'all'`, via `VITE_BRIDGE_LOCAL` → `RELAY_ONLY` in `web/src/config.ts` |

It also **implies `VOICE_INVOKER=local`** (`LocalInvoker` POSTs to `{VOICE_RUNTIME_URL}/invocations` instead of calling `invoke_agent_runtime`). `VOICE_INVOKER` remains the fine-grained selector: set it explicitly and it wins, so `BRIDGE_LOCAL=1 VOICE_INVOKER=agentcore` points a local control plane at a deployed runtime.

The runtime uses **one entrypoint for both modes** — `_handle_offer` branches in place; there is no forked local handler.

### It cannot be turned on in a deploy

Two independent locks:

1. `BRIDGE_LOCAL` / `VOICE_INVOKER` appear nowhere in `amplify/voice-runtime.ts` or `VOICE_CONFIG` (asserted in `amplify/*.test.ts`).
2. The settings validator **raises when `ENV=production`** — even a leaked flag stops the container at start rather than silently disabling the relay-only filter.

### Zero-AWS is machine-enforced

Under the flag, the validator refuses `STT_PROVIDER=transcribe`, `TTS_PROVIDER=polly`, `LLM_PROVIDER=bedrock` and `REFEREE_PROVIDER=bedrock`, naming each offender and its fix:

```
BRIDGE_LOCAL=1 forbids AWS-backed providers so local runs make zero AWS calls:
STT_PROVIDER=transcribe (use together); TTS_PROVIDER=polly (use inworld)
```

(`REFEREE_PROVIDER` is read from `os.environ`: it belongs to `bridge.config`, and `voice_kit` must never import `bridge`.)

## Verifying: the Tier-1 smoke

The transport is verified independently of the browser, by a real aiortc handshake:

```bash
npm run dev                                  # in another terminal
.venv/bin/python scripts/local_voice_smoke.py
```

It asserts `/start` returned no ICE servers, that the answer carries a **non-relay** candidate (proving the filter was skipped), that ICE reaches `connected`, that a v1 game event arrives over the data channel and parses to an *object* (a double-encoded payload leaves audio working while the SPA drops every event — kit gotcha #34), and that `/end` is clean. Non-zero exit on any failure. Not wired into CI — it needs three live processes and the provider keys.

### Tier 2: a full game in the browser

Open the SPA and play, checking the paths tests cannot reach: a turn that clears a transient action, the Restraint instant-fail, the timer-expiry fail, and Play Again resetting cleanly (a brand-new `session_id`, a clean board). Kill the runtime process mid-game to check the connection-lost end state. See [`../frontend/voice-integration.md`](../frontend/voice-integration.md) for what each of those exercises.

## Gotchas

- **`BRIDGE_LOCAL` must be in the environment before `api.main` is imported.** `create_voice_router` resolves `get_invoker()` when the router is *built*, and `api/main.py` builds it at import. That is the whole reason `api/local.py` exists; it uses `setdefault`, so an explicit `BRIDGE_LOCAL=0` still wins.
- **`configure(**overrides)` bypasses the validator** (it assigns with `setattr`) — a pre-existing gap, unchanged here.
- **`VoiceKitSettings` now reads a repo-root `.env` too** (env-file order: package-adjacent, then CWD; later wins, real env vars beat both). New settings tests pass `_env_file=None` so a developer's `.env` cannot leak into assertions.
