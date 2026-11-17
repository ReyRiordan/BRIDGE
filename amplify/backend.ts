// ---------------------------------------------------------------------------
// BRIDGE backend — Amplify Gen 2 entry point.
//
// Provisions three things in one custom stack:
//   1. The control-plane API Lambda (FastAPI under the Lambda Web Adapter, as
//      a container image) behind a Function URL.
//   2. The voice runtime (KVS channel + AgentCore container on the managed
//      PUBLIC network + IAM), via the vendored addVoiceRuntime() module.
//   3. The `BridgeSessions` DynamoDB table: one session record per case. The
//      API creates items; the runtime only updates them.
//
// No Amplify auth/data categories: `defineBackend({})` exists only to give us
// the Amplify app, the backend identifier, and the outputs file.
//
// Everything here validates ONLY at deploy time. See
// docs/backend/deployment.md for the runbook and the topology rationale.
// ---------------------------------------------------------------------------

import { defineBackend } from '@aws-amplify/backend';
import { CDKContextKey, ParameterPathConversions } from '@aws-amplify/platform-core';
import type { BackendIdentifier } from '@aws-amplify/plugin-types';
import { Duration, RemovalPolicy } from 'aws-cdk-lib';
import { AttributeType, BillingMode, ProjectionType, Table } from 'aws-cdk-lib/aws-dynamodb';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import {
  Architecture,
  DockerImageCode,
  DockerImageFunction,
  FunctionUrlAuthType,
} from 'aws-cdk-lib/aws-lambda';
import { PolicyStatement } from 'aws-cdk-lib/aws-iam';

import {
  ALLOWED_ORIGINS,
  API_IMAGE_EXCLUDE,
  API_LAMBDA,
  API_SECRET_NAMES,
  SECRET_NAMES,
  SESSION_SUMMARY_ATTRIBUTES,
  SESSIONS_INDEX,
  VOICE_CONFIG,
  VOICE_IMAGE_EXCLUDE,
  voiceRuntimeName,
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
// Session records
//
// Study data: RETAIN so a stack teardown or a bad deploy can never delete it,
// PITR for a bad write. In this per-backend stack, so sandbox and main get
// separate tables. Deletions are by hand (see docs/backend/deployment.md).
// ---------------------------------------------------------------------------
const sessionsTable = new Table(stack, 'BridgeSessions', {
  partitionKey: { name: 'session_id', type: AttributeType.STRING },
  billingMode: BillingMode.PAY_PER_REQUEST,
  pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
  removalPolicy: RemovalPolicy.RETAIN,
});
// A constant partition (`kind` = "session") is fine at study scale; the
// results page filters client-side.
sessionsTable.addGlobalSecondaryIndex({
  indexName: SESSIONS_INDEX,
  partitionKey: { name: 'kind', type: AttributeType.STRING },
  sortKey: { name: 'created_at', type: AttributeType.STRING },
  projectionType: ProjectionType.INCLUDE,
  nonKeyAttributes: SESSION_SUMMARY_ATTRIBUTES,
});

// ---------------------------------------------------------------------------
// Control-plane API Lambda
//
// A container-image function, not a zip: api/Dockerfile.api layers deps before
// source, so pip becomes a cached layer keyed on api/requirements.txt +
// runtime/pyproject.toml instead of a cold `pip install` on every asset-hash
// change. The context is the repo root — the image needs api/, resources/ and
// the vendored runtime/voice_kit.
//
// No `runtime`/`handler` props: CDK sets PackageType FROM_IMAGE itself and
// throws if either is passed. The entry point is the image's uvicorn CMD.
// ---------------------------------------------------------------------------
const apiFn = new DockerImageFunction(stack, 'BridgeApi', {
  architecture: Architecture.ARM_64,
  memorySize: API_LAMBDA.memorySizeMb,
  timeout: Duration.seconds(API_LAMBDA.timeoutSeconds),
  // Deliberately no SCENARIOS_DIR: VOICE_CONFIG's value is the *voice*
  // container's path. This image COPYs resources/ next to api/, which is what
  // api/scenario.py's default resolves to.
  environment: {
    ALLOWED_ORIGINS: ALLOWED_ORIGINS.join(','),
    SESSIONS_TABLE: sessionsTable.tableName,
    // The results-page password, resolved from SSM by voice_kit.config at
    // cold start (api/admin.py imports it explicitly).
    SECRETS_FROM_SSM: API_SECRET_NAMES.join(','),
    SECRETS_SSM_PREFIXES: secretsSsmPrefixes,
  },
  code: DockerImageCode.fromImageAsset('.', {
    file: 'api/Dockerfile.api',
    platform: Platform.LINUX_ARM64,
    // Subtracts the voice image's tree so a runtime/bridge edit leaves this
    // asset's hash alone — see API_IMAGE_EXCLUDE.
    exclude: API_IMAGE_EXCLUDE,
  }),
});

// Function URL, not API Gateway: no auth, throttling, or custom-domain needs
// while auth is out of scope. `cors` is deliberately NOT configured — FastAPI's
// CORSMiddleware owns CORS, and configuring both duplicates the headers, which
// browsers reject.
// Create (POST /sessions) plus the reads the results page needs. `grant`
// covers the index ARNs too, which Query on the GSI requires.
sessionsTable.grant(apiFn, 'dynamodb:PutItem', 'dynamodb:GetItem', 'dynamodb:Query');

// The API's SSM secrets, scoped to the same prefixes as the runtime's grant in
// voice-runtime.ts (SecureStrings under the AWS-managed key need no kms grant).
apiFn.addToRolePolicy(
  new PolicyStatement({
    actions: ['ssm:GetParameter'],
    resources: secretsSsmPrefixes
      .split(',')
      .map((prefix) => `arn:aws:ssm:${stack.region}:${stack.account}:parameter${prefix}/*`),
  }),
);

const apiFnUrl = apiFn.addFunctionUrl({ authType: FunctionUrlAuthType.NONE });

// ---------------------------------------------------------------------------
// Voice runtime
// ---------------------------------------------------------------------------
addVoiceRuntime({
  stack,
  // Per-backend, like channelName below: AgentCore runtime names are unique per
  // ACCOUNT, so the kit's 'VoiceRuntime' default makes the second backend fail
  // with AlreadyExists while sandbox and branch stacks coexist.
  runtimeName: voiceRuntimeName(backendId),
  // Repo root: the image needs runtime/ AND resources/. The root .dockerignore
  // keeps the context small.
  dockerContext: '.',
  dockerfile: 'runtime/Dockerfile.voice',
  // Subtracts api/ so an API-only change doesn't rebuild + re-push the pipecat
  // image and restart the runtime.
  dockerExclude: VOICE_IMAGE_EXCLUDE,
  // Derived per backend: the sandbox and branch stacks coexist briefly, and
  // both sides look the channel up by NAME, which must be unique per account.
  channelName: `${stack.stackName}-signaling`,
  invokers: [apiFn],
  environment: { ...VOICE_CONFIG, SESSIONS_TABLE: sessionsTable.tableName },
  // UpdateItem only, on the table itself: the runtime fills records in by id
  // (conditionally, so it can never create one) and can read none of them.
  extraRuntimePolicies: [
    new PolicyStatement({
      actions: ['dynamodb:UpdateItem'],
      resources: [sessionsTable.tableArn],
    }),
  ],
  secretsFromSsm: SECRET_NAMES.join(','),
  secretsSsmPrefixes,
});

// Surfaced to the SPA through amplify_outputs.json (the Hosting build fetches
// it with `ampx generate outputs` and copies it into web/public/).
backend.addOutput({ custom: { apiUrl: apiFnUrl.url } });
