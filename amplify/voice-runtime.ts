// ---------------------------------------------------------------------------
// Voice runtime infra — Pipecat pipeline on AWS Bedrock AgentCore Runtime.
//
// Drop-in for an Amplify Gen 2 backend.ts (CDK). Call addVoiceRuntime(...) from
// your backend.ts and it provisions: the KVS signaling channel, the AgentCore
// runtime built from Dockerfile.voice (on AgentCore's managed PUBLIC network —
// no VPC, no NAT gateway), all task-role grants, and the API-Lambda-side wiring
// (grantInvoke + env vars + browser-side KVS grants).
// docs/backend/voice-kit/03-infrastructure.md carries the worked example and
// explains every grant. Wired from backend.ts.
//
// The real-time WebRTC voice pipeline is the one workload that can't run on
// Lambda (stateful, streaming, long sessions). It runs as a Pipecat pipeline on
// a Bedrock AgentCore Runtime. The API Lambda proxies WebRTC signaling to it
// (invoke_agent_runtime, IAM/SigV4); media flows browser<->runtime directly
// over KVS-managed TURN.
//
// This whole block is validated only at deploy time (`ampx sandbox` — confirm
// the runtime reaches READY).
// ---------------------------------------------------------------------------

import { CfnOutput, Duration, Stack } from 'aws-cdk-lib';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import { Effect, PolicyStatement } from 'aws-cdk-lib/aws-iam';
import { CfnSignalingChannel } from 'aws-cdk-lib/aws-kinesisvideo';
import {
  AgentRuntimeArtifact,
  Runtime as AgentCoreRuntime,
  RuntimeNetworkConfiguration,
} from 'aws-cdk-lib/aws-bedrockagentcore';
import type { Function as LambdaFunction } from 'aws-cdk-lib/aws-lambda';

export interface VoiceRuntimeProps {
  stack: Stack;
  /**
   * Docker build context. In BRIDGE this is the REPO ROOT ('.'): the image
   * needs both runtime/ and resources/, and the root .dockerignore keeps the
   * context small.
   */
  dockerContext: string;
  /**
   * Dockerfile path, relative to `dockerContext` (default 'Dockerfile.voice').
   * BRIDGE passes 'runtime/Dockerfile.voice' because the context is the root.
   */
  dockerfile?: string;
  /**
   * Extra excludes for the image asset's staged context, layered on top of the
   * context root's `.dockerignore` (CDK merges the two). CDK derives the asset
   * HASH from the staged copy, so this is what keeps the hash independent of
   * trees this image does not COPY — set it when the same context root feeds a
   * second image asset (BRIDGE excludes `api/`). Never list a tree the
   * Dockerfile COPYs: the hash then freezes and the deploy ships stale code
   * with no error.
   */
  dockerExclude?: string[];
  /** Runtime construct name (default 'VoiceRuntime'). */
  runtimeName?: string;
  /**
   * Explicit KVS signaling channel name (default 'VoiceKitSignalingChannel').
   * The name is set explicitly because CloudFormation exposes no name
   * attribute and the runtime looks the channel up by name via
   * describe_signaling_channel(ChannelName=...). Must be unique per account.
   */
  channelName?: string;
  /** API Lambdas that proxy signaling to the runtime (usually [apiLambda]). */
  invokers: LambdaFunction[];
  /** Extra env vars for the runtime (e.g. your table names, SYSTEM_PROMPT). */
  environment?: Record<string, string>;
  /** Comma-joined secret names + SSM path prefixes for keyless cold-start
   * secret resolution (see voice_kit/config.py _export_ssm_secrets). */
  secretsFromSsm?: string;
  secretsSsmPrefixes?: string;
  /** Extra task-role policies (e.g. DynamoDB reads for your context provider,
   * writes for your transcript sink). */
  extraRuntimePolicies?: PolicyStatement[];
}

export function addVoiceRuntime(props: VoiceRuntimeProps) {
  const { stack } = props;
  const channelName = props.channelName ?? 'VoiceKitSignalingChannel';

  // KVS signaling channel — the runtime lazily calls GetIceServerConfig against
  // this channel (per session) to obtain managed-TURN credentials (relay-only).
  // SINGLE_MASTER is the only supported channel type.
  const kvsChannel = new CfnSignalingChannel(stack, 'VoiceKitKvsChannel', {
    name: channelName,
    type: 'SINGLE_MASTER',
  });

  // AgentCore Runtime: CDK builds the ARM64 image from Dockerfile.voice -> ECR
  // and provisions the runtime on AgentCore's managed PUBLIC network — outbound
  // internet (AI providers, KVS TURN) with no inbound exposure (the only way in
  // is IAM-authed invoke_agent_runtime), and no VPC/NAT gateway to pay for.
  // Inbound auth defaults to IAM/SigV4 (no authorizerConfiguration) — keyless,
  // and required for session affinity (invoke_agent_runtime(runtimeSessionId=...)
  // pins all signaling round-trips to one container).
  const voiceRuntime = new AgentCoreRuntime(stack, 'VoiceKitRuntime', {
    runtimeName: props.runtimeName ?? 'VoiceRuntime',
    agentRuntimeArtifact: AgentRuntimeArtifact.fromAsset(props.dockerContext, {
      file: props.dockerfile ?? 'Dockerfile.voice',
      platform: Platform.LINUX_ARM64,
      exclude: props.dockerExclude,
    }),
    networkConfiguration: RuntimeNetworkConfiguration.usingPublicNetwork(),
    // Self-terminate runaway containers (the pipeline also self-terminates at
    // SESSION_TIME_LIMIT_MINUTES; this is a backstop ceiling).
    lifecycleConfiguration: { maxLifetime: Duration.hours(1) },
    environmentVariables: {
      ENV: 'production',
      // KVS signaling channel the runtime fetches ICE servers from (lazily).
      KVS_CHANNEL_NAME: channelName,
      // Secrets (OPENROUTER_API_KEY, INWORLD_API_KEY, TOGETHER_API_KEY,
      // AWS_BEDROCK_BASE_URL, ...) arrive via SSM paths, fetched at cold start
      // by voice_kit/config.py — never as plain-text values here.
      ...(props.secretsFromSsm ? { SECRETS_FROM_SSM: props.secretsFromSsm } : {}),
      ...(props.secretsSsmPrefixes
        ? { SECRETS_SSM_PREFIXES: props.secretsSsmPrefixes }
        : {}),
      //
      // DO NOT inject AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY here. boto3's default
      // credential chain prefers env-var creds over the runtime's IAM task role, so
      // injecting them (even as placeholders) shadows the task role for EVERY boto3
      // client in the container — including the keyless KVS client in
      // voice_kit/kvs.py, which then fails DescribeSignalingChannel with
      // UnrecognizedClientException ("security token invalid") and crashes the
      // signaling handler. KVS / Transcribe / Polly / Bedrock all authorize via
      // the task role grants below — never add static AWS keys here.
      //
      // Pipeline config (LLM_PROVIDER, LLM_MODEL, LLM_REASONING, LLM_PROVIDERS,
      // STT_PROVIDER, TTS_PROVIDER, TTS_VOICE, TTS_MODEL,
      // SESSION_TIME_LIMIT_MINUTES, SYSTEM_PROMPT, ...) comes in via
      // props.environment — keep one AI_CONFIG-style constants object in your
      // backend.ts as the single source of truth (all values must be strings).
      ...props.environment,
      AWS_REGION: stack.region,
    },
  });

  // Runtime task-role grants (keyless):
  //  - KVS signaling on the channel ARN: discover the endpoint + fetch ICE/TURN
  //    creds (GetIceServerConfig) for relay-only WebRTC.
  voiceRuntime.grant(
    [
      'kinesisvideo:GetSignalingChannelEndpoint',
      'kinesisvideo:DescribeSignalingChannel',
    ],
    [kvsChannel.attrArn],
  );
  // GetIceServerConfig is issued against the channel's per-call signaling endpoint
  // (kinesis-video-signaling); it authorizes on the channel ARN.
  voiceRuntime.grant(['kinesisvideo:GetIceServerConfig'], [kvsChannel.attrArn]);

  //  - Amazon Transcribe streaming for in-region STT (resource-less action).
  //  - Amazon Polly streaming synthesis for PollyTTS (resource-less; PollyTTS
  //    calls StartSpeechSynthesisStream — the generative-engine streaming API,
  //    NOT SynthesizeSpeech; granting only SynthesizeSpeech fails with
  //    AccessDenied).
  voiceRuntime.role.addToPrincipalPolicy(
    new PolicyStatement({
      effect: Effect.ALLOW,
      actions: [
        'transcribe:StartStreamTranscription',
        'polly:StartSpeechSynthesisStream',
      ],
      resources: ['*'],
    }),
  );

  //  - Bedrock (bedrock-mantle) CreateInference for LLM_PROVIDER=bedrock
  //    (no-op cost when OpenRouter is the active provider). SigV4-signed on the
  //    task role; the mantle OpenAI-compatible endpoint authorizes via
  //    bedrock-mantle:*, not bedrock:InvokeModel.
  voiceRuntime.role.addToPrincipalPolicy(
    new PolicyStatement({
      effect: Effect.ALLOW,
      actions: [
        'bedrock-mantle:CreateInference',
        'aws-marketplace:Subscribe',
        'aws-marketplace:ViewSubscriptions',
      ],
      resources: ['*'],
    }),
  );

  //  - ssm:GetParameter scoped to the secret prefixes (SecureStrings under the
  //    AWS-managed `aws/ssm` KMS key need no separate kms:Decrypt grant).
  if (props.secretsSsmPrefixes) {
    voiceRuntime.role.addToPrincipalPolicy(
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: ['ssm:GetParameter'],
        resources: props.secretsSsmPrefixes
          .split(',')
          .map(
            (prefix) =>
              `arn:aws:ssm:${stack.region}:${stack.account}:parameter${prefix}/*`,
          ),
      }),
    );
  }

  //  - Host-specific grants (context-provider reads, transcript-sink writes).
  for (const policy of props.extraRuntimePolicies ?? []) {
    voiceRuntime.role.addToPrincipalPolicy(policy);
  }

  // API-Lambda wiring: each invoker proxies signaling to the runtime
  // (server-to-server, IAM/SigV4) and needs the runtime ARN + channel name.
  for (const fn of props.invokers) {
    voiceRuntime.grantInvoke(fn);
    fn.addEnvironment('VOICE_RUNTIME_ARN', voiceRuntime.agentRuntimeArn);
    fn.addEnvironment('KVS_CHANNEL_NAME', channelName);

    // The API Lambda also fetches KVS managed-TURN ICE servers for the BROWSER
    // (returned by the /start endpoint). Relay-only WebRTC requires BOTH peers
    // to hold their own TURN allocation — without the browser's own ICE servers
    // it gathers only private host candidates and the runtime's relay
    // CHANNEL_BIND is rejected with "403 Forbidden IP" and ICE silently stalls.
    // Same three KVS actions the runtime has.
    fn.addToRolePolicy(
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: [
          'kinesisvideo:GetSignalingChannelEndpoint',
          'kinesisvideo:DescribeSignalingChannel',
          'kinesisvideo:GetIceServerConfig',
        ],
        resources: [kvsChannel.attrArn],
      }),
    );
  }

  new CfnOutput(stack, 'VoiceRuntimeArn', {
    value: voiceRuntime.agentRuntimeArn,
  });

  return { runtime: voiceRuntime, kvsChannel, channelName };
}
