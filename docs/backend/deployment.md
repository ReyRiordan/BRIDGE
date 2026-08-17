# Deployment

How BRIDGE gets to AWS: the environment topology, what deploys from where, and the runbook. Companion to `amplify/backend.ts`; the voice runtime's own internals are in `voice-kit/03-infrastructure.md` and its failure modes in `voice-kit/05-gotchas.md`.

## Target account

| | |
|---|---|
| AWS profile | `compass-test` → account **893361712219**, region **us-east-1** |
| GitHub repo for Hosting | `ReyRiordan/MEWAI-BD` (pre-rename name) |
| Bootstrap | CDK-bootstrapped ✓ |

**Availability zones.** AgentCore supports only the physical zones `use1-az1/az2/az4`, and the letter → physical mapping is randomized per account. Verified for this account on 2026-08-16 with `aws ec2 describe-availability-zones`:

| Letter | Physical |
|---|---|
| `us-east-1a` | `use1-az1` |
| `us-east-1b` | `use1-az2` |
| `us-east-1c` | `use1-az4` |

Those three letters are pinned in `amplify/constants.ts` (`AVAILABILITY_ZONES`). **Re-verify before deploying into any other account** — wrong letters fail runtime creation with "subnets are in unsupported availability zones" and roll the whole stack back.

## Topology

One real environment: the **`main` branch stack**. A sandbox is used only to shake out the first deploy and is deleted afterwards, so exactly one NAT gateway and one AgentCore runtime are ever billed.

```
Amplify Hosting (web/dist)  ──fetch──>  Lambda Function URL  ──invoke_agent_runtime──>  AgentCore runtime
        ▲ built from amplify.yml            ▲ api/main.py (Mangum)         ▲ runtime/Dockerfile.voice, in-VPC
        │                                   └── amplify_outputs.json custom.apiUrl
        └── frontend only (no Docker in the build image)
```

**Backend deploys always run from a local machine.** Amplify Hosting's build images have no Docker, and both the AgentCore image asset and the Lambda bundling need it. Hosting therefore builds the SPA only (`amplify.yml` has no `backend` phase); the branch backend goes up via `ampx pipeline-deploy` run locally.

**API exposure** is a Lambda **Function URL** with `authType: NONE` — no API Gateway, since there are no auth, throttling, or custom-domain needs while auth is out of scope. The URL is configured with **no CORS block**: FastAPI's `CORSMiddleware` in `api/main.py` owns CORS, and configuring both duplicates the response headers, which browsers reject.

**SPA → API wiring.** `backend.addOutput({ custom: { apiUrl } })` puts the Function URL into `amplify_outputs.json`. A sandbox writes that file locally; the Hosting build fetches it with `npx ampx generate outputs` and copies it into `web/public/`, so the SPA can `fetch('/amplify_outputs.json')` at runtime. Neither copy is committed.

## Secrets

The three provider API keys (`OPENROUTER_API_KEY`, `TOGETHER_API_KEY`, `INWORLD_API_KEY`, listed in `SECRET_NAMES`) live in SSM Parameter Store and are resolved by the runtime at cold start via `SECRETS_FROM_SSM` / `SECRETS_SSM_PREFIXES` — never as plain-text env values. `backend.ts` derives the prefixes from the backend identifier with `ParameterPathConversions`, covering both the branch/sandbox-scoped path and the app-shared one.

```bash
npx ampx sandbox secret set OPENROUTER_API_KEY --profile compass-test   # ×3
```

For the branch, set the same three in the Amplify console under App settings → Secrets.

> Never put `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the runtime environment — they shadow the task role for every boto3 client in the container (gotcha #1).

## Runbook

Order matters at steps 5 → 6 → 7.

1. **Pre-deploy gate** — the image builds and answers:
   ```bash
   docker build --platform linux/arm64 -f runtime/Dockerfile.voice -t bridge-voice .
   docker run --rm -p 8080:8080 bridge-voice   # then: curl localhost:8080/ping
   ```
2. **Sandbox** — `npx ampx sandbox --profile compass-test`. Watch for AZ `UPDATE_FAILED`; confirm the AgentCore runtime reaches **READY**.
3. **Secrets** — `ampx sandbox secret set` ×3 (above).
4. **Verify** — `aws lambda get-function-configuration` shows `VOICE_RUNTIME_ARN` + `KVS_CHANNEL_NAME`; `curl <fnUrl>/health` returns `{"status":"ok","scenario_loaded":true}`; one direct `aws bedrock-agentcore invoke-agent-runtime` dummy payload triggers a cold start whose CloudWatch logs show SSM secret resolution with no `None`-key errors and no `UnrecognizedClientException`.
5. **Hosting** — in the Amplify console create the app against `ReyRiordan/MEWAI-BD` `main`, build spec from the root `amplify.yml`, frontend-only. Set the three secrets for the branch.
6. **Branch backend** — `CI=1 npx ampx pipeline-deploy --app-id <appId> --branch main --profile compass-test`. Must precede the first Hosting build, since `ampx generate outputs` needs a backend to read.
7. **End to end** — trigger the Hosting build, open the SPA, and `fetch` `<apiUrl>/health` from the browser console: that proves reachability *and* CORS. Add the Hosting origin to `ALLOWED_ORIGINS` in `amplify/constants.ts` and redeploy the backend once (chicken-and-egg; expected).
8. **Tear down the sandbox** — `npx ampx sandbox delete --profile compass-test`. The branch stack is the single remaining environment.

## Cost

One NAT gateway is the fixed floor (~$32/month, always on) — the reason the sandbox is deleted rather than kept alongside the branch stack. Media relays through KVS managed TURN (billed per streaming minute), not the NAT. AgentCore containers scale per `runtimeSessionId` and are bounded by `maxLifetime: 1h`.

## Gotchas

- `ampx pipeline-deploy` needs `CI=1` and an app + branch that already exist.
- The Docker asset rebuild is the slow step of every deploy. The root `.dockerignore` and `API_ASSET_EXCLUDE` keep the hashes stable so unchanged code skips it.
- If `npm ci` skipped install scripts (esbuild, `@parcel/watcher`), `ampx` will fail to start — approve them with `npm install-scripts approve <pkg>`.
- `api/main.py` is the [Rewrite C] placeholder. It proves bundling and CORS only, and gets replaced wholesale.
