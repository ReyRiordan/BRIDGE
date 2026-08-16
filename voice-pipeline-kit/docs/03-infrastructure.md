# Infrastructure

Prose companion to `infra/voice-runtime.ts` (the copyable CDK module — see `infra/README.md` for wiring). Everything here validates **only at deploy time**.

## Why AgentCore at all

The real-time WebRTC pipeline is the one workload that can't run on Lambda: stateful (peer connections in memory), streaming, and sessions run for the whole conversation limit. AgentCore Runtime runs the ARM64 container directly, keeps `/ping`+`/invocations` semantics, and gives per-`runtimeSessionId` container affinity — which the design depends on (gotcha #6).

## VPC

- 2–3 AZs, **pinned explicitly** to AgentCore-supported zones (physical `use1-az1/az2/az4`; letter mapping randomized per account — verify with `aws ec2 describe-availability-zones`). Wrong AZs roll back the whole stack (gotcha #4).
- 1 NAT gateway; the runtime lives in `PRIVATE_WITH_EGRESS` subnets: off the public internet, but able to reach external AI providers (OpenRouter/Inworld/Together) and the KVS TURN endpoint via NAT → IGW.
- Security group: outbound UDP to anywhere (`allowToAnyIpv4(Port.allUdp())`) for WebRTC media to KVS-managed TURN.
- Optional free **DynamoDB gateway endpoint** (`addDynamoDbEndpoint: true`): if your context provider / transcript sink use DynamoDB, their traffic bypasses the NAT — lower latency on the hottest per-turn write path, no NAT data cost.

## KVS signaling channel

One `CfnSignalingChannel`, type `SINGLE_MASTER` (the only supported type), with an **explicit name** — CFN exposes no name attribute and both the runtime and the API look the channel up by name (gotcha #12). The channel is used purely for `GetIceServerConfig` (managed TURN credentials); no KVS signaling websockets are involved — signaling rides the AgentCore invoke path.

## AgentCore Runtime

- Image: CDK `AgentRuntimeArtifact.fromAsset(dockerContext, { file: 'Dockerfile.voice', platform: LINUX_ARM64 })` → ECR. ARM64 is required.
- Network: `RuntimeNetworkConfiguration.usingVpc` on the private subnets.
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

- One NAT gateway is the main fixed cost; media itself relays through KVS TURN (billed per TURN streaming minute), not the NAT.
- Containers scale per `runtimeSessionId`; `maxLifetime` bounds runaways. The pipeline's idle timeout ends abandoned sessions earlier.
