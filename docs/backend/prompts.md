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
    --provider bedrock --model openai.gpt-oss-120b --reasoning low
```

`referee_eval.py` runs the production path (real scenario, real prompt,
`build_referee_payload` / `build_response_format` / `build_referee_llm`) over the
cases in `runtime/evals/cases/referee.json` — `{name, utterance, escalation,
expected}`, mixing the few-shot anchors with paraphrases, near-misses for the
strict escalating actions, a dedup case, and the actions no few-shot covers.
Eight of the 23 were written after the prompt was tuned, to catch exactly the
overfitting that tuning invites: they restate rules the earlier cases already
cover, in wording the prompt has never seen. It
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

Run both after touching either prompt. These rules exist only because an eval
caught the failure, and each is easy to regress:

- The referee's "ambient qualities are not actions" rule (rule 7) — without it,
  any calm or polite utterance (small talk included) scored
  `Verbal Communication`.
- The "Reading a desc" section, and the "some descs name a way of speaking"
  carve-out under it. Descs are terse (`E.g. "We need to do this now"`), and a
  weaker model reads them as required phrasings rather than behaviours, which
  loses real detections. The carve-out is the counterweight to rule 7: without
  it, rule 7 swallows the two actions whose descs are themselves about manner,
  `Verbal Communication` and `Authoritative tone`.
- Rule 4's decided-versus-hedged distinction. An earlier phrasing disqualified
  future-tense mentions outright, which read "I'm going to have security hold
  him down" as hypothetical and let a restraint order go uncharged.
- Rule 5, checking every action rather than stopping at the first hit. Low-effort
  models return only the most salient action of a multi-action utterance; this
  rule alone moved the 45-run suite from 39 to 42.
- The patient's TTS/no-emote rule and the escalation-0 row's history caveat —
  the persona otherwise emitted `*shifts uncomfortably*` (spoken verbatim by the
  TTS) and answered history questions once fully calm.

### Measured referee behaviour

69 runs each (23 cases x 3), one sitting, us-east-1, all on the current prompt.
Accuracy is exact set match on the detected action types; latency is the whole
`chat()` round trip.

| Provider / model / effort | Passed | Median | p95 | Max |
|---|---|---|---|---|
| openrouter `anthropic/claude-haiku-4.5` / `none` | 68/69 | 1.12s | 2.88s | 7.47s |
| **bedrock `openai.gpt-oss-120b` / `low` (deployed)** | **64/69** | **0.79s** | **2.81s** | **6.59s** |

Earlier sweeps on the narrower 15-case suite, kept because they are why the
deployed effort is `low` and not something else:

| Config | Passed | Median | p95 | Max |
|---|---|---|---|---|
| gpt-oss-120b / `low` | 37/45 | 0.79s | 2.81s | 4.93s |
| gpt-oss-120b / `medium` | 40/45 | 2.43s | 5.30s | 7.15s |
| gpt-oss-120b / `high` | 37/45 | 6.80s | 25.26s | 60.50s |

Three things that sweep settled.

**JSON held.** Zero parse failures in several hundred Bedrock calls with
`response_format` ignored throughout. Prompt-enforced structure is good enough on
this model; that question is closed.

**`high` is unusable and `medium` is not worth it.** `high` runs the tail three
times past `REFEREE_TIMEOUT_SECONDS` (one run hit a 60s client timeout) and buys
no accuracy — its extra failures are phantom `Verbal Communication`. `medium`
peaked at 7.15s against the 7s fail-open budget. `low` is the fastest *and*, on
the current prompt, the most accurate of the three.

**One model-specific gap remains.** `openai.gpt-oss-120b` under-detects
`Authoritative tone` when a heavier escalating action sits in the same utterance:
it scores the `Restraint` or `Force IV` and drops the command that ordered it.
Reproduced on two independently worded utterances, at every effort level, and it
survives both the completeness rule and a matching few-shot. All five of the
Bedrock failures above are this one pattern. Asked to reason aloud the model gets
it right, so it is a recall problem at low effort, not a judgement one.

Consequence for the game: a student who orders staff to restrain the patient is
charged for the restraint (+10) but not the +2 command. Under-charging, never
over-charging. The next lever is the scenario's own `desc` for that action —
`E.g. "We need to do this now"` is the narrowest desc in the set, and it is
student-facing text, so widening it is a game-content decision rather than a
prompt fix.

The prompt is shared with whatever provider is configured, so it is checked
against both. The rewrite that fixed the Bedrock failures left haiku slightly
better than it was, which is what keeps a provider rollback safe.
