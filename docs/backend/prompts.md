# Prompts

The two LLM calls in a turn — the referee and the patient — and the manual evals
that keep them honest. Everything here lives in `resources/` (COPYed into the
runtime image at `/app/resources`) and is assembled by `runtime/bridge/`.

| File | Loaded by | Consumed by |
|---|---|---|
| `resources/referee.txt` | `config.load_referee_prompt()` (`REFEREE_PROMPT_PATH`) | `RefereeProcessor` |
| `resources/patient.txt` | `config.load_patient_prompt_template()` (`PATIENT_PROMPT_PATH`) | `patient.build_patient_prompt()` |
| `resources/patient.json` | `config.load_patient_case()` (`PATIENT_CASE_PATH`) | `patient.build_patient_prompt()` |

## Referee

Per turn the referee gets a JSON user message built by
`referee.build_referee_payload()`:

```json
{"utterance": "...", "escalation": 5,
 "actions": [{"type": "...", "desc": "...", "escalates": false}]}
```

- **Point values never leave the server.** The model sees only `escalates` — the
  sign of `point_change` — so it cannot tempt us into trusting numbers it echoed
  back. The prompt keys its strictness on that flag ("be liberal with
  `escalates: false`, strict with `escalates: true`) rather than naming actions,
  which is what makes **adding an action to `scenario_1.json` require no prompt
  edit**: the enum, the action list, and `escalates` are all generated.
- The reply is constrained by a strict `json_schema` whose `type` enum is
  generated from the scenario (`referee.build_response_format()`); only action
  types come back, and the server looks the points up itself.
- **Fails open.** Timeout, provider error, malformed JSON — the turn scores as
  no detections and the patient still replies.

## Patient

`patient.build_patient_prompt(scenario)` = the `patient.txt` template with
`{patient_name}` substituted (via `str.replace` — the body is curly-brace-heavy),
plus a rendered `=== PATIENT CASE DETAILS ===` section carrying
`<demographics>`, `<behavior>` (when present), `<chief_concern>` and
`<free_information>`.

**`locked_information` is never rendered.** History-taking is out of scope for
the current sim, which is only about de-escalation. The items stay in
`patient.json` as seed data for that future feature; withholding them from the
prompt entirely is a stronger guarantee than any instruction, since the model
cannot leak what it never sees. `patient.txt` carries the matching standing
rule: never volunteer clinical history, deflect history questions in a manner
consistent with the current escalation level — at *every* level, including 0.

Behaviour is conditioned on the **per-turn marker** `[CURRENT ESCALATION: n/max]`
(`patient.turn_context()`), injected by the kit immediately before the student's
message; the template's escalation table maps bands to terseness and
cooperativeness.

## Evals — `runtime/evals/`

Manual only. They cost money, need the network, and are nondeterministic, so
they live **outside `runtime/tests/`** and CI never executes them (it does lint
them: `ruff check runtime`). On the OpenRouter defaults both need
`OPENROUTER_API_KEY`; aimed at Bedrock, the referee eval needs AWS credentials
and `AWS_BEDROCK_BASE_URL` instead.

```bash
python3 runtime/evals/referee_eval.py     # pass/fail table + per-case latency; nonzero on failure
python3 runtime/evals/patient_probe.py    # replies at escalation 10/8/5/2/0, eyeball only

# The deployed pairing (amplify/constants.ts). --provider/--model/--reasoning
# override the REFEREE_* trio the script otherwise reads from the environment.
python3 runtime/evals/referee_eval.py \
    --provider bedrock --model openai.gpt-oss-120b --reasoning medium
```

`referee_eval.py` runs the production path (real scenario, real prompt,
`build_referee_payload` / `build_response_format` / `build_referee_llm`) over the
cases in `runtime/evals/cases/referee.json` — `{name, utterance, escalation,
expected}`, mixing the few-shot anchors with paraphrases, near-misses for the
strict escalating actions, a dedup case, and the actions no few-shot covers. It
also prints median/p95/max latency, which matters: the referee sits on the
serial critical path before the patient reply and fails open past
`REFEREE_TIMEOUT_SECONDS`, so the p95 is what says whether that budget holds.

The eval parses replies with `parse_referee_verdict`, the same function the
pipeline uses. That is deliberate: on Bedrock `response_format` is ignored, so
the eval's parse-failure count is a real measurement of whether the prompt alone
holds the JSON shape. Every parse failure is a turn a student would silently
lose.

`patient_probe.py` sends one de-escalating line and one history question at each
escalation level and prints the replies side by side — a 30-second check that
terseness tracks escalation and that history is deflected everywhere.

Run both after touching either prompt. Two prompt rules exist only because the
evals caught the failure and are easy to regress:

- The referee's "ambient qualities are not actions" rule — without it, any calm
  or polite utterance (small talk included) scored `Verbal Communication`.
- The patient's TTS/no-emote rule and the escalation-0 row's history caveat —
  the persona otherwise emitted `*shifts uncomfortably*` (spoken verbatim by the
  TTS) and answered history questions once fully calm.

Referee latency, measured on `anthropic/claude-haiku-4.5` at
`reasoning.effort: none`: ~1.1s median, ~2s worst case over 45 runs. That is not
the deployed pairing any more — the deploy runs `openai.gpt-oss-120b` on Bedrock
at `medium`, which adds reasoning latency to every turn. Re-measure with the
`--provider bedrock` invocation above before trusting the 7s budget, and raise
`REFEREE_TIMEOUT_SECONDS` only if the p95 says so.
