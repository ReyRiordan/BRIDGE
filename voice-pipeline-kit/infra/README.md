# Infra: AgentCore voice runtime for Amplify Gen 2

`voice-runtime.ts` is a self-contained CDK module for an Amplify Gen 2 `amplify/` folder. It provisions everything the voice pipeline needs: VPC (1 NAT, private subnets), KVS signaling channel, the AgentCore Runtime built from `Dockerfile.voice`, all IAM grants, and API-Lambda wiring.

## Install

1. Copy `voice-runtime.ts` into your `amplify/` folder (e.g. `amplify/voice-runtime.ts`).
2. Copy `backend/Dockerfile.voice`, `backend/requirements-voice.txt`, and `backend/voice_kit/` into your backend docker context (the directory you pass as `dockerContext`).
3. Wire it up in `backend.ts`:

```ts
import { addVoiceRuntime } from './voice-runtime';

// Keep ONE constants object as the source of truth for pipeline config
// (all values must be strings — these become container env vars):
const VOICE_CONFIG = {
  LLM_PROVIDER: 'bedrock',            // 'openrouter' | 'bedrock'
  LLM_MODEL: 'openai.gpt-oss-120b',
  LLM_REASONING: 'low',
  LLM_PROVIDERS: '',                  // OpenRouter routing prefs; ignored on bedrock
  STT_PROVIDER: 'transcribe',         // 'transcribe' | 'together' (together = off-AWS, dev only)
  TTS_PROVIDER: 'polly',              // 'polly' | 'inworld'
  TTS_VOICE: 'Ruth',
  SESSION_TIME_LIMIT_MINUTES: '30',
  SYSTEM_PROMPT: 'You are a friendly voice assistant.', // or register a context provider
};

const { runtime } = addVoiceRuntime({
  stack,
  // VERIFY FIRST: aws ec2 describe-availability-zones — AgentCore supports only
  // physical use1-az1/az2/az4 and the letter mapping is randomized per account.
  availabilityZones: ['us-east-1a', 'us-east-1b', 'us-east-1c'],
  dockerContext: 'amplify/backend',
  invokers: [apiLambda],
  environment: VOICE_CONFIG,
  // Keyless secrets: names + SSM path prefixes (set values with
  // `npx ampx sandbox secret set OPENROUTER_API_KEY` etc.)
  secretsFromSsm: ['OPENROUTER_API_KEY', 'AWS_BEDROCK_BASE_URL', 'TOGETHER_API_KEY', 'INWORLD_API_KEY'].join(','),
  secretsSsmPrefixes: mySsmPrefixes, // see below
  // Grants for YOUR context provider / transcript sink, e.g.:
  // extraRuntimePolicies: [/* dynamodb reads/writes */],
  // addDynamoDbEndpoint: true,
});
```

For Amplify-managed secret paths, build the prefixes with `@aws-amplify/platform-core`:

```ts
import { ParameterPathConversions } from '@aws-amplify/platform-core';
const mySsmPrefixes = [
  ParameterPathConversions.toParameterPrefix(backendId),           // branch/sandbox-scoped
  ParameterPathConversions.toParameterPrefix(backendId.namespace), // app-shared
].join(',');
```

## Notes

- Requires an `aws-cdk-lib` new enough to ship `aws-cdk-lib/aws-bedrockagentcore` (the `Runtime`/`AgentRuntimeArtifact`/`RuntimeNetworkConfiguration` constructs).
- Everything in this module validates **only at deploy time**. First deploy: `npx ampx sandbox`, watch for AZ `UPDATE_FAILED`, confirm the runtime reaches `READY`. See `docs/04-deploy-runbook.md`.
- Every IAM action is explained in `docs/03-infrastructure.md`; the failure symptoms live in `docs/05-gotchas.md`.
