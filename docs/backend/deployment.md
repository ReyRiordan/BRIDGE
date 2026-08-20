# Deployment

How BRIDGE gets to AWS: the environment topology, what deploys from where, and the runbook. Companion to `amplify/backend.ts`; the voice runtime's own internals are in `voice-kit/03-infrastructure.md` and its failure modes in `voice-kit/05-gotchas.md`.

## Target account

| | |
|---|---|
| AWS profile | `compass-test` → account **893361712219**, region **us-east-1** |
| GitHub repo for Hosting | `ReyRiordan/BRIDGE` |
| Bootstrap | CDK-bootstrapped ✓ |

## Quick reference: backend redeploy

Amplify app id: **`d8vcc5ya6qjw1`**. To redeploy the `main` branch backend, run from the repo root (`ampx` resolves `amplify/backend.ts` from the working directory) with Docker running:

```bash
CI=1 AWS_PROFILE=compass-test npx ampx pipeline-deploy --app-id d8vcc5ya6qjw1 --branch main
```

## Topology

One real environment: the **`main` branch stack**. A sandbox is used only to shake out the first deploy and is deleted afterwards, so exactly one AgentCore runtime is ever billed.

```
Amplify Hosting (web/dist)  ──fetch──>  Lambda Function URL  ──invoke_agent_runtime──>  AgentCore runtime
        ▲ built from amplify.yml            ▲ api/main.py (FastAPI + LWA container)  ▲ runtime/Dockerfile.voice, managed PUBLIC network
        │                                   └── amplify_outputs.json custom.apiUrl
        └── frontend only (no Docker in the build image)
```

**Backend deploys always run from a local machine.** Amplify Hosting's build images have no Docker, and both image assets — the AgentCore runtime and the API Lambda — need it. Hosting therefore builds the SPA only (`amplify.yml` has no `backend` phase); the branch backend goes up via `ampx pipeline-deploy` run locally.

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

1. **Pre-deploy gate** — both images build and answer:
   ```bash
   docker build --platform linux/arm64 -f runtime/Dockerfile.voice -t bridge-voice .
   docker run --rm -p 8080:8080 bridge-voice   # then: curl localhost:8080/ping

   docker build --platform linux/arm64 -f api/Dockerfile.api -t bridge-api .
   docker run --rm -p 8000:8000 -e ALLOWED_ORIGINS=http://localhost:5173 bridge-api
   curl -s localhost:8000/health     # {"status":"ok","scenario_loaded":true}
   curl -s localhost:8000/scenario | head
   ```
   The API image installs the kit with `--no-deps`, so an undeclared import surfaces
   only at container start — which is exactly what the `docker run` above catches.
2. **Sandbox** — `npx ampx sandbox --profile compass-test`. Confirm the AgentCore runtime reaches **READY**.
3. **Secrets** — `ampx sandbox secret set` ×3 (above). The command prompts on a TTY and rejects an empty value, so in a non-interactive shell pipe the value in instead: `printf '%s' "$KEY" | npx ampx sandbox secret set NAME --profile compass-test`.
4. **Verify** —
   - `aws lambda get-function-configuration` shows `VOICE_RUNTIME_ARN` + `KVS_CHANNEL_NAME`.
   - `curl <fnUrl>/health` returns `{"status":"ok","scenario_loaded":true}` (this is the proof that `resources/` really is in the image).
   - `aws ssm describe-parameters` lists the three keys under the runtime's first `SECRETS_SSM_PREFIXES` path.
   - `aws bedrock-agentcore invoke-agent-runtime` with a **fresh** `runtimeSessionId` (containers are pinned per session — reusing one gets you a warm container from before the secrets existed) triggers a cold start. CloudWatch should show "Application startup complete" and no `UnrecognizedClientException` / `None`-key errors.
   - `_export_ssm_secrets()` logs nothing on success and swallows `ParameterNotFound`, so there is no positive log line to grep for. What the clean start *does* prove is that the read did not raise: it runs at import time (`config.py`), so an `AccessDenied` would stop the container before it ever served `/ping`. Confirm the grant itself with `aws iam simulate-principal-policy --action-names ssm:GetParameter` against the runtime role, including a negative control outside the prefix (expect `implicitDeny`).
5. **Hosting** — in the Amplify console create the app against the GitHub repo's `main`, build spec from the root `amplify.yml`, frontend-only. Leave **"my app is a monorepo" unchecked**: the build spec handles `web/` itself via `npm --prefix`, and that option expects the different `applications:`/`appRoot` format.

   The app also needs an **IAM service role**, or the build runs as Amplify's own service account and `ampx generate outputs` fails with `AccessDenied` on `cloudformation:GetTemplateSummary` — the giveaway is that the account id in that error is AWS's, not yours. Hosting here only *reads* backend outputs, so a minimal role is enough:

   ```bash
   aws iam create-role --role-name BridgeAmplifyServiceRole \
     --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"amplify.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
   # inline policy — cloudformation:DescribeStacks/GetTemplateSummary/ListStackResources/DescribeStackResources
   #   on stack/amplify-<appId>-*/*, plus amplify:GetApp/GetBranch/ListBranches on apps/<appId>*
   aws amplify update-app --app-id <appId> \
     --iam-service-role-arn arn:aws:iam::<account>:role/BridgeAmplifyServiceRole
   ```

   AWS's docs name the managed `AmplifyBackendDeployFullAccess`, which is not available in every account; the only managed alternative here, `AdministratorAccess-Amplify`, is far broader than a frontend-only build warrants. Widen the role only if Hosting is ever made to deploy backends.
6. **Branch backend** — `CI=1 AWS_PROFILE=compass-test npx ampx pipeline-deploy --app-id <appId> --branch main`. Note `pipeline-deploy` takes **no `--profile` flag** (it is built for CI and reads credentials from the environment) — pass the profile as `AWS_PROFILE`. Must precede the first Hosting build, since `ampx generate outputs` needs a backend to read.
7. **End to end** — trigger the Hosting build, open the SPA, and `fetch` `<apiUrl>/health` from the browser console: that proves reachability *and* CORS. Hosting origins are deterministic (`https://<branch>.<appId>.amplifyapp.com`), so `ALLOWED_ORIGINS` can be set the moment the app id is known — no need to wait for the first build and redeploy afterwards. Custom domains and preview branches are extra origins and must be added explicitly.
8. **Tear down the sandbox** — `npx ampx sandbox delete --profile compass-test`. The branch stack is the single remaining environment.

## Cost

No always-on network cost: the runtime uses AgentCore's managed PUBLIC network, so there is no VPC, NAT gateway, or Elastic IP (an earlier revision ran in-VPC and paid ~$32/month for the NAT — gotcha #4 in `voice-kit/05-gotchas.md`). Media relays through KVS managed TURN (billed per streaming minute). AgentCore containers scale per `runtimeSessionId` and are bounded by `maxLifetime: 1h`.

## Gotchas

- `ampx pipeline-deploy` needs `CI=1` and an app + branch that already exist.
- **Name every per-account resource off the backend identifier.** Both the KVS channel and the AgentCore runtime are unique per account, and sandbox + branch stacks coexist, so the kit's shared defaults (`VoiceKitSignalingChannel`, `VoiceRuntime`) make the second deploy fail with `AlreadyExists`. `voiceRuntimeName()` in `constants.ts` also sanitizes: AgentCore accepts only `[a-zA-Z][a-zA-Z0-9_]{0,47}`, so hyphenated stack names cannot be passed through.
- A stack left in `ROLLBACK_COMPLETE` cannot be updated — delete it before redeploying.
- **Never `.dockerignore` a tree an image COPYs.** CDK merges the context root's `.dockerignore` with the per-asset `exclude` prop, stages the tree, and derives the asset hash from the *staged copy*. An ignored tree therefore freezes the hash and the deploy ships **stale code with no error** — the worst failure mode here, because nothing fails. `api/` is subtracted from the voice asset only, via `VOICE_IMAGE_EXCLUDE`, never in `.dockerignore`.
- **Keep CDK's own output out of the asset source.** Both image assets are rooted at the repo while CDK stages into `.amplify/artifacts/cdk.out/` *inside* that root, so staging copies its output into itself until `ENAMETOOLONG`. `.dockerignore` needs `.amplify/` and `cdk.out/` — and note `amplify/` does **not** match `.amplify/`.
- **The image build/push is the slow step of every deploy, so guard the hashes.** Two image assets share the repo root as their context: `.dockerignore` is the shared subtraction, and each asset's `exclude` (`API_IMAGE_EXCLUDE` / `VOICE_IMAGE_EXCLUDE` in `constants.ts`) subtracts the other's tree so their hashes never cross-bleed. Junk patterns there are recursive on purpose — a local `pip install ./runtime` regenerates `runtime/build/` + `runtime/*.egg-info` and root-anchored patterns would let that untracked junk bust both hashes. Inside the API image, pip is a cached layer keyed on exactly `api/requirements.txt` + `runtime/pyproject.toml`, so an `api/` or `voice_kit/` edit re-runs only a `COPY`.
- **Both Lambdas' images push to the CDK bootstrap ECR repo** (`cdk-hnb659fds-container-assets-<account>-<region>`) — no per-asset repo and no bootstrap change. Untagged old images accumulate there; a lifecycle policy is not configured yet. The API image also pays image-pull latency (~1–2s) on the first invoke after a deploy; the untried levers if that ever bites are `AWS_LWA_ASYNC_INIT=true` (changes readiness semantics) and a 512→1024 memory bump (CPU scales with memory, and this cold start is import-bound).
- Use `npm install`, not `npm ci`, at the repo root. `@aws-amplify/backend` pulls `@aws-amplify/data-construct` and `@aws-amplify/graphql-api-construct`, whose bundled nested dependencies npm reports as "Missing from lock file" even immediately after a clean install. Amplify CI runs `npm install` plus a `git diff --exit-code package-lock.json` drift check; `web/` is unaffected and still uses `npm ci`.
- **`esbuild` is a direct root devDependency on purpose.** CDK's `NodejsFunction` bundling (the `AmplifyBranchLinker` asset) probes for `node_modules/.bin/esbuild` and, if it is missing, silently falls back to bundling in an amd64 Docker image — emulated under QEMU on Apple silicon, and slow. Nothing imports esbuild directly; the dependency exists so npm hoists that binary. It is pinned to the version tsx already depends on so npm dedupes to one copy.
- If the install skipped install scripts (esbuild, `@parcel/watcher`), `ampx` will fail to start and the esbuild binary will not exist — approve them with `npm install-scripts approve <pkg>`. The esbuild failure is silent, so verify after any dependency change:

  ```bash
  node_modules/.bin/esbuild --version   # expect 0.25.x; "no such file" means deploys are on emulated Docker
  ```
