# Infrastructure

Prose companion to `amplify/voice-runtime.ts` — the self-contained CDK module that provisions everything the voice pipeline needs: the KVS signaling channel, the AgentCore Runtime built from `runtime/Dockerfile.voice` (on AgentCore's managed **PUBLIC** network — no VPC or NAT gateway), all IAM grants, and API-Lambda wiring. Everything here validates **only at deploy time**.

## Wiring it up (worked example)

`backend.ts` ([Rewrite B]) calls `addVoiceRuntime(...)`:

```ts
import { addVoiceRuntime } from './voice-runtime';

// ONE constants object as the source of truth for pipeline config
// (all values must be strings — these become container env vars):
const VOICE_CONFIG = {
  LLM_PROVIDER: 'bedrock',            // 'openrouter' | 'bedrock'
  LLM_MODEL: 'openai.gpt-oss-120b',
  LLM_REASONING: 'low',
  LLM_PROVIDERS: '',                  // OpenRouter routing prefs; ignored on bedrock
  STT_PROVIDER: 'transcribe',         // 'transcribe' | 'together' (together = off-AWS, dev only)
  TTS_PROVIDER: 'polly',              // 'polly' | 'inworld'
  TTS_VOICE: 'Ruth',
  SESSION_TIME_LIMIT_MINUTES: '30',  // app cap (informational to the kit)
  IDLE_TIMEOUT_SECS: '180',          // pipeline self-termination backstop
};

const { runtime } = addVoiceRuntime({
  stack,
  // The image needs runtime/ AND resources/, so the context is the REPO ROOT
  // and the Dockerfile path is relative to it. The root .dockerignore keeps the
  // context from picking up the legacy trees.
  dockerContext: '.',
  dockerfile: 'runtime/Dockerfile.voice',
  // Only when the same context root feeds a SECOND image asset: subtract the
  // other image's tree so the two hashes stay independent.
  dockerExclude: ['api/'],
  invokers: [apiLambda],
  environment: VOICE_CONFIG,
  // Keyless secrets: names + SSM path prefixes (set values with
  // `npx ampx sandbox secret set OPENROUTER_API_KEY` etc.)
  secretsFromSsm: ['OPENROUTER_API_KEY', 'AWS_BEDROCK_BASE_URL', 'TOGETHER_API_KEY', 'INWORLD_API_KEY'].join(','),
  secretsSsmPrefixes: ssmPrefixes,
  // Grants for the game engine's context provider / transcript sink, e.g.:
  // extraRuntimePolicies: [/* dynamodb reads/writes */],
});
```

For Amplify-managed secret paths, build the prefixes with `@aws-amplify/platform-core`:

```ts
import { ParameterPathConversions } from '@aws-amplify/platform-core';
const ssmPrefixes = [
  ParameterPathConversions.toParameterPrefix(backendId),           // branch/sandbox-scoped
  ParameterPathConversions.toParameterPrefix(backendId.namespace), // app-shared
].join(',');
```

Requires an `aws-cdk-lib` new enough to ship `aws-cdk-lib/aws-bedrockagentcore` (the `Runtime` / `AgentRuntimeArtifact` / `RuntimeNetworkConfiguration` constructs) — pinned in the root `package.json`.

## Why AgentCore at all

The real-time WebRTC pipeline is the one workload that can't run on Lambda: stateful (peer connections in memory), streaming, and sessions run for the whole conversation limit. AgentCore Runtime runs the ARM64 container directly, keeps `/ping`+`/invocations` semantics, and gives per-`runtimeSessionId` container affinity — which the design depends on (gotcha #6).

## Network: managed PUBLIC, no VPC

The runtime uses `RuntimeNetworkConfiguration.usingPublicNetwork()` — AgentCore's default managed network. Outbound internet (external AI providers, the KVS TURN endpoint, SSM) works directly; there is **no inbound exposure** in either mode, because the only way to reach the container is IAM-authed `invoke_agent_runtime` through the AgentCore service endpoint. A customer VPC would buy nothing here and costs a NAT gateway (~$32/month + data processing) plus AZ-pinning constraints (AgentCore VPC mode supports only physical `use1-az1/az2/az4`, with per-account letter randomization). An earlier revision ran in-VPC; it was removed for exactly that cost.

## KVS signaling channel

One `CfnSignalingChannel`, type `SINGLE_MASTER` (the only supported type), with an **explicit name** — CFN exposes no name attribute and both the runtime and the API look the channel up by name (gotcha #12). The channel is used purely for `GetIceServerConfig` (managed TURN credentials); no KVS signaling websockets are involved — signaling rides the AgentCore invoke path.

## AgentCore Runtime

- Image: CDK `AgentRuntimeArtifact.fromAsset(dockerContext, { file, platform: LINUX_ARM64, exclude })` → ECR. ARM64 is required. BRIDGE builds from the repo root with `file: 'runtime/Dockerfile.voice'` so `resources/` (scenario + prompts) lands in the image.
- `dockerExclude` layers extra excludes on top of the context root's `.dockerignore` (CDK merges the two) and exists for HASH STABILITY: the hash is derived from the staged copy, so when a second image asset shares the context root, each must subtract the other's tree or an unrelated edit rebuilds and re-pushes this image and restarts the runtime. BRIDGE passes `['api/']`. Never list a tree the Dockerfile COPYs — the hash freezes and the deploy ships stale code with no error.
- Network: `RuntimeNetworkConfiguration.usingPublicNetwork()` (see the network section above).
- Inbound auth: **IAM/SigV4** (no `authorizerConfiguration`) — keyless from the API host and required for `runtimeSessionId` affinity.
- `lifecycleConfiguration.maxLifetime`: 1 hour — the backstop ceiling above the pipeline's own idle-timeout self-termination.
- Environment: pipeline config (all strings) + `KVS_CHANNEL_NAME` + `SECRETS_FROM_SSM`/`SECRETS_SSM_PREFIXES`. **Never static AWS keys** (gotcha #1).

## Task-role grants (runtime), with reasons

| Grant | Resource | Why |
|---|---|---|
| `kinesisvideo:GetSignalingChannelEndpoint`, `kinesisvideo:DescribeSignalingChannel` | channel ARN | Resolve the channel + its HTTPS signaling endpoint (per session, lazy) |
| `kinesisvideo:GetIceServerConfig` | channel ARN | Fetch managed-TURN credentials; issued against the per-call signaling endpoint but authorizes on the channel ARN |
| `transcribe:StartStreamTranscription` | `*` (resource-less action) | AmazonTranscribeSTT streaming |
| `polly:StartSpeechSynthesisStream` | `*` | PollyTTS generative streaming — NOT `SynthesizeSpeech` (gotcha #2) |
| `bedrock-mantle:CreateInference` (+ `aws-marketplace:Subscribe/ViewSubscriptions`) | `*` | Bedrock chat-completions via SigV4 when `LLM_PROVIDER=bedrock`; no-op cost under OpenRouter |
| `ssm:GetParameter` | your secret prefixes | Cold-start secret resolution (SecureStrings under the AWS-managed `aws/ssm` key need no extra `kms:Decrypt`) |
| *(yours via `extraRuntimePolicies`)* | — | Whatever your context provider reads and your transcript sink writes |

## API-host wiring (per invoker)

| Item | Why |
|---|---|
| `runtime.grantInvoke(fn)` | `bedrock-agentcore:InvokeAgentRuntime` for the `/signal` proxy |
| env `VOICE_RUNTIME_ARN`, `KVS_CHANNEL_NAME` | Consumed by `voice_kit` control-plane settings |
| The same three `kinesisvideo:*` actions on the channel ARN | `/start` fetches the **browser's** own TURN allocation — relay-only WebRTC needs both peers to hold one (gotcha #9) |

## Cost & scale notes

- No always-on network cost: the public network mode has no NAT gateway or EIP. Media relays through KVS TURN (billed per TURN streaming minute); AgentCore bills per active session.
- Containers scale per `runtimeSessionId`; `maxLifetime` bounds runaways. The pipeline's idle timeout ends abandoned sessions earlier.

## First deploy

`npx ampx sandbox`, confirm the runtime reaches `READY`. Step-by-step: `04-deploy-runbook.md`. Failure symptoms: `05-gotchas.md`.
