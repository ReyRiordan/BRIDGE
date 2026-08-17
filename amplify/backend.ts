// ---------------------------------------------------------------------------
// BRIDGE backend — Amplify Gen 2 entry point.
//
// Provisions two things in one custom stack:
//   1. The control-plane API Lambda (FastAPI + Mangum) behind a Function URL.
//   2. The voice runtime (VPC + KVS channel + AgentCore container + IAM), via
//      the vendored addVoiceRuntime() module.
//
// No auth/data resources: `defineBackend({})` exists only to give us the
// Amplify app, the backend identifier, and the outputs file.
//
// Everything here validates ONLY at deploy time. See
// docs/backend/deployment.md for the runbook and the topology rationale.
// ---------------------------------------------------------------------------

import { defineBackend } from '@aws-amplify/backend';
import { CDKContextKey, ParameterPathConversions } from '@aws-amplify/platform-core';
import type { BackendIdentifier } from '@aws-amplify/plugin-types';
import { DockerImage, Duration } from 'aws-cdk-lib';
import {
  Architecture,
  Code,
  Function as LambdaFunction,
  FunctionUrlAuthType,
  Runtime as LambdaRuntime,
} from 'aws-cdk-lib/aws-lambda';

import {
  ALLOWED_ORIGINS,
  API_ASSET_EXCLUDE,
  API_LAMBDA,
  AVAILABILITY_ZONES,
  SECRET_NAMES,
  VOICE_CONFIG,
} from './constants';
import { addVoiceRuntime } from './voice-runtime';

const backend = defineBackend({});
const stack = backend.createStack('bridge-voice');

// ---------------------------------------------------------------------------
// SSM secret prefixes
//
// The runtime resolves its provider API keys from SSM at cold start. Amplify
// stores `ampx sandbox secret set` / console secrets under paths derived from
// the backend identifier, which CDK carries in context.
// ---------------------------------------------------------------------------
const backendId: BackendIdentifier = {
  namespace: stack.node.getContext(CDKContextKey.BACKEND_NAMESPACE),
  name: stack.node.getContext(CDKContextKey.BACKEND_NAME),
  type: stack.node.getContext(CDKContextKey.DEPLOYMENT_TYPE),
};
const secretsSsmPrefixes = [
  ParameterPathConversions.toParameterPrefix(backendId), // branch/sandbox-scoped
  ParameterPathConversions.toParameterPrefix(backendId.namespace), // app-shared
].join(',');

// ---------------------------------------------------------------------------
// Control-plane API Lambda
//
// Bundled with a plain `Code.fromAsset(repo root)` rather than PythonFunction:
// PythonFunction's commandHooks run in a container that mounts only the `entry`
// directory, so they cannot reach `resources/` outside `api/`. Same mechanism,
// controllable inputs.
// ---------------------------------------------------------------------------
const apiFn = new LambdaFunction(stack, 'BridgeApi', {
  runtime: LambdaRuntime.PYTHON_3_11,
  architecture: Architecture.ARM_64,
  handler: API_LAMBDA.handler,
  memorySize: API_LAMBDA.memorySizeMb,
  timeout: Duration.seconds(API_LAMBDA.timeoutSeconds),
  // Deliberately no SCENARIO_PATH: VOICE_CONFIG's value is the *container's*
  // /app/resources path. The Lambda reads the copy inside its own bundle.
  environment: { ALLOWED_ORIGINS: ALLOWED_ORIGINS.join(',') },
  code: Code.fromAsset('.', {
    exclude: API_ASSET_EXCLUDE,
    bundling: {
      image: DockerImage.fromRegistry('public.ecr.aws/lambda/python:3.11'),
      platform: 'linux/arm64',
      // The tooling pins (ruff/pytest/httpx) live in api/requirements.txt for
      // CI to grep; they have no business in the deployed package. resources/
      // is copied in so /health (and [Rewrite C]'s /scenario) can read the
      // scenario JSON from the bundle.
      command: [
        'bash',
        '-c',
        [
          "sed '/^ruff==/d;/^pytest==/d;/^httpx==/d' api/requirements.txt > /tmp/requirements.txt",
          'pip install --no-cache-dir -r /tmp/requirements.txt --target /asset-output',
          'cp -r api /asset-output/api',
          'cp -r resources /asset-output/resources',
        ].join(' && '),
      ],
    },
  }),
});

// Function URL, not API Gateway: no auth, throttling, or custom-domain needs
// while auth is out of scope. `cors` is deliberately NOT configured — FastAPI's
// CORSMiddleware owns CORS, and configuring both duplicates the headers, which
// browsers reject.
const apiFnUrl = apiFn.addFunctionUrl({ authType: FunctionUrlAuthType.NONE });

// ---------------------------------------------------------------------------
// Voice runtime
// ---------------------------------------------------------------------------
addVoiceRuntime({
  stack,
  availabilityZones: AVAILABILITY_ZONES,
  // Repo root: the image needs runtime/ AND resources/. The root .dockerignore
  // keeps the context small.
  dockerContext: '.',
  dockerfile: 'runtime/Dockerfile.voice',
  // Derived per backend: the sandbox and branch stacks coexist briefly, and
  // both sides look the channel up by NAME, which must be unique per account.
  channelName: `${stack.stackName}-signaling`,
  invokers: [apiFn],
  environment: VOICE_CONFIG,
  secretsFromSsm: SECRET_NAMES.join(','),
  secretsSsmPrefixes,
});

// Surfaced to the SPA through amplify_outputs.json (the Hosting build fetches
// it with `ampx generate outputs` and copies it into web/public/).
backend.addOutput({ custom: { apiUrl: apiFnUrl.url } });
