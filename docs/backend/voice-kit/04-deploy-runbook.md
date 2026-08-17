# Deploy runbook

Ordered steps for the first deploy into a new account, plus the failure table. The infra block validates **only at deploy time** — none of it is exercised by tests.

## 1. Verify AgentCore AZs (once per account)

```sh
aws ec2 describe-availability-zones \
  --query 'AvailabilityZones[].{letter:ZoneName,physical:ZoneId}' --output table
```

AgentCore supports only physical `use1-az1`, `use1-az2`, `use1-az4`. Map those to your account's letters and pass them to `addVoiceRuntime({ availabilityZones: [...] })`. Wrong letters → runtime creation fails with "subnets are in unsupported availability zones" and the stack rolls back.

## 2. Put secrets in SSM

For each enabled provider (`OPENROUTER_API_KEY`, `AWS_BEDROCK_BASE_URL`, `INWORLD_API_KEY`, `TOGETHER_API_KEY`):

```sh
npx ampx sandbox secret set OPENROUTER_API_KEY     # Amplify-managed path
```

Match the names/prefixes you pass as `secretsFromSsm` / `secretsSsmPrefixes`.

## 3. Local build smoke (the pre-deploy gate)

```sh
cd <docker-context>
docker build --platform linux/arm64 -f Dockerfile.voice -t voice-kit .
docker run --rm -p 8080:8080 voice-kit
curl localhost:8080/ping        # expect a healthy response
```

A green `/ping` proves: the dependency set resolves on ARM64, `cv2`/`libGL` are satisfied, and the app module (your wrapper or `voice_kit.runtime`) imports. This is exactly the failure class that only surfaces in the image.

## 4. Deploy

```sh
npx ampx sandbox        # or your pipeline-deploy
```

Watch for:
- AZ-related `UPDATE_FAILED` on the VPC/runtime (step 1 was wrong).
- The AgentCore runtime reaching **READY** in the console.

## 5. Post-deploy checklist

1. `/start` returns `runtime_session_id` (40 chars) **and a non-empty `ice_servers`** — an empty list means the API role's KVS grants or `KVS_CHANNEL_NAME` are wrong (check API logs for the warning).
2. One `start` → `signal` round trip returns an SDP answer whose only candidates are `typ relay` lines (`grep 'a=candidate' | grep -v relay` should be empty).
3. A real call connects: ICE reaches `connected` within ~10 s, you hear TTS, and transcript turns arrive on the data channel.
4. CloudWatch (runtime log group): no `UnrecognizedClientException`, no `set_wakeup_fd`, no `Missing module: cv2`.
5. If you registered a transcript handler: turns appear in your store *during* the call, and `/end` returns them.
6. Reconnect drill: kill the network mid-call; the UI should retry with a fresh `runtime_session_id` and the agent should resume with prior context.

## 6. Rollback / teardown

- Rollback: redeploy the previous branch/commit (image + infra roll together). The runtime is stateless between sessions — nothing to migrate.
- Teardown: destroy the stack (`npx ampx sandbox delete` or pipeline teardown). The KVS channel and VPC delete with it; ECR images may linger per your retention.

## Symptom → cause table

| Symptom | Likely cause | Gotcha |
|---|---|---|
| Browser shows bare "Network Error" on `/signal` | Unhandled control-plane exception → CORS-less 500 (missing `register_exception_handlers`, or an unwrapped botocore error); or `runtime_session_id` < 33 chars (`ParamValidationError`) | #22, #5 |
| Stack rollback: "subnets are in unsupported availability zones" | Wrong AZ letters for this account | #4 |
| ICE stalls forever; runtime logs "403 Forbidden IP" | Browser built its peer connection without its own `ice_servers` (empty list from `/start`, or missing API-role KVS grants) | #9 |
| `UnrecognizedClientException` in runtime logs, `/signal` 502 | Static `AWS_ACCESS_KEY_ID`/`SECRET` injected into the runtime env, shadowing the task role | #1 |
| Call connects, agent silent, no audio track in answer | Pipeline/transport built after `get_answer()` (regression in the connection callback) | #16 |
| Call connects, agent replies in transcript but no audio | TTS emitting bare `AudioRawFrame`s (dropped by the output transport); or Polly IAM has `SynthesizeSpeech` instead of `StartSpeechSynthesisStream` | #17, #2 |
| Nothing ever transcribes | Gating on `UserStartedSpeakingFrame` instead of `VADUserStartedSpeakingFrame`, or `VADProcessor` missing from the chain | #18 |
| First word of each utterance missing | Pre-roll disabled/shrunk (`STT_PREROLL_MS`) | #19 |
| Pipeline dies: "set_wakeup_fd only works in main thread" | `PipelineRunner` signal handling enabled on the daemon loop | #15 |
| Container crash at start: `ImportError: Missing module: cv2` / `libGL.so.1` | Image missing `libgl1`/`libglib2.0-0` (edited Dockerfile apt list) | #26 |
| Pipeline freezes right after the answer returns | Event loop not `run_forever` in the daemon thread | #14 |
| Duplicate transcript turns after a reconnect | One-pipeline-per-session guard removed/broken | #20 |
| Bedrock 403 InvalidSignature | Payload re-serialized between signing and sending (`json=` instead of `data=body`), or manual `Host` header | #3 |
| Random reconnects "lose" the conversation | uvicorn workers > 1 sharding in-process state | #7 |
