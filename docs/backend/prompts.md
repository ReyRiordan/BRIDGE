# Prompts

The two LLM calls in a turn — the referee and the patient — and the manual evals
that keep them honest. Everything here lives in `resources/` (COPYed into the
runtime image at `/app/resources`) and is assembled by `runtime/bridge/`.

| File | Loaded by | Consumed by |
|---|---|---|
| `resources/referee.txt` (shared) | `config.load_referee_prompt()` (`REFEREE_PROMPT_PATH`) | `RefereeProcessor` |
| `resources/scenarios/<id>/patient.txt` | `config.load_patient_prompt_template(id)` (`SCENARIOS_DIR`) | `patient.build_patient_prompt()` |
| `resources/scenarios/<id>/patient.json` | `config.load_patient_case(id)` (`SCENARIOS_DIR`) | `patient.build_patient_prompt()` |

## Referee

Per turn the referee gets a JSON user message built by
`referee.build_referee_payload()`:

```json
{"utterance": "...", "escalation": 5,
 "actions": [{"type": "...", "desc": "...", "escalates": false}]}
```

- **Point values never leave the server.** The model sees only `escalates` — the
  sign of `point_change`, or `true` for an END SIMULATION action (`point_change: 0`,
  `ends_simulation: true`), the most consequential call it makes — so it cannot tempt us into trusting numbers it echoed
  back. The prompt keys its strictness on that flag ("be liberal with
  `escalates: false`, strict with `escalates: true`) rather than naming actions,
  which is what makes **adding an action to a `scenario.json` require no prompt
  edit**: the enum, the action list, and `escalates` are all generated.
- The reply is constrained by a strict `json_schema` whose `type` enum is
  generated from the scenario (`referee.build_response_format()`); only action
  types come back, and the server looks the points up itself.
- **Fails open.** Timeout, provider error, malformed JSON — the turn scores as
  no detections and the patient still replies.

## Patient

`patient.build_patient_prompt(scenario)` = the scenario's own `patient.txt` template
(read by `scenario["id"]`) with
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

The template is per scenario because it carries the setting (emergency
department, clinic exam room, inpatient ward) and a scenario-specific escalation
table. In scenario_2/3 that table also carries the source docs' non-visual scene
changes, mapped onto bands, so the patient voices them ("I'm not trying to be
difficult, I just can't lose this job" at 3–4). Visual scene changes are action
layers, not prompt text.

Behaviour is conditioned on the **per-turn marker** `[CURRENT ESCALATION: n/max]`
(`patient.turn_context()`), injected by the kit immediately before the student's
message; the template's escalation table maps bands to terseness and
cooperativeness.

## Evals — `runtime/evals/`

Manual only. They cost money, need the network, and are nondeterministic, so
they live **outside `runtime/tests/`** and CI never executes them (it does lint
them: `ruff check runtime`). On the OpenRouter defaults both need
`OPENROUTER_API_KEY`; aimed at Bedrock, the referee eval needs AWS credentials,
`AWS_BEDROCK_BASE_URL` and the matching `AWS_BEDROCK_SIGV4_SERVICE` instead.

```bash
python runtime/evals/referee_eval.py [--scenario scenario_2]   # pass/fail table + latency; nonzero on failure
python runtime/evals/patient_probe.py [--scenario scenario_3]  # replies at escalation 10/8/5/2/0, eyeball only

# The deployed pairing (amplify/constants.ts). --provider/--model/--reasoning
# override the REFEREE_* trio the script otherwise reads from the environment;
# the endpoint and its signing service come from the environment.
AWS_BEDROCK_BASE_URL=https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1 \
AWS_BEDROCK_SIGV4_SERVICE=bedrock \
python runtime/evals/referee_eval.py --scenario scenario_1 \
    --provider bedrock --model us.openai.gpt-6-luna --reasoning low --repeat 3
```

`referee_eval.py` runs the production path (the real scenario config, real prompt,
`build_referee_payload` / `build_response_format` / `build_referee_llm`) over the
scenario's cases in `runtime/evals/cases/referee_<scenario>.json` — `{name,
utterance, escalation, expected}`. Every scenario's set covers each of its
actions at least once, plus paraphrases, near-misses for the strict escalating
actions (hedged or conditional security threats above all, since the END action
ends the game), multi-action combinations, and neutral lines that must score
nothing. In scenario_1, eight of the 23 were written after the prompt was tuned,
to catch exactly the overfitting that tuning invites: they restate rules the
earlier cases already cover, in wording the prompt has never seen. It
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

### Model choice

Both agents run `us.openai.gpt-6-luna` on bedrock-runtime chat-completions
(patient `medium`, referee `low`). It replaced `openai.gpt-oss-120b` on
bedrock-mantle, which **ran on past its reply** on roughly one patient turn in
five: after the real answer it emitted further messages — imagined later turns,
`[NO RESPONSE]` / `[END OF RESPONSE]`, or a copy of the next
`[CURRENT ESCALATION: n/max]` marker — which the endpoint concatenated into one
`content` string with no separator (the giveaway is a sentence glued to the next,
`already.[CURRENT…` or `beforehand.I just`). The TTS spoke all of it, and the
history kept it. Moving the marker into the user message, changing effort, or
switching to Converse only changed the rate; on bedrock-runtime gpt-oss leaks
its `<reasoning>` into `content` on every turn. See `voice-kit/05-gotchas.md` #38.

**Patient**, scenario_2, 8 conversations x 7 turns = 56 replies each. An artifact
is a glued or bracketed continuation, a tag, or an emote:

| Model / effort | Path | Artifacts | p50 | p90 |
|---|---|---|---|---|
| gpt-oss-120b / `medium` | mantle chat-completions | 10/56 | 1.09s | 2.35s |
| Claude Haiku 5.5 | Converse | 1/56 (an emote) | 1.05s | 1.38s |
| **GPT-6 Luna / `low`** | bedrock-runtime chat-completions | **0/56** | **0.56s** | **0.75s** |

A harder pass then ran Luna at `low` and `medium` across all three scenarios (8
adversarial lines each — blaming the patient, "calm down", security threats, "are
you an AI?", a prompt-injection request for the case file, stacked history
questions at every level including 1–2, emote bait; 96 replies per effort).
Both: zero artifacts, zero replies over two sentences, zero history disclosed,
character held throughout. The difference is the top bands: `medium` yells and
goes monosyllabic at 9–10 ("No! Get away!") as the tables ask, where `low` stays
in calm full sentences — and those bands are where two scenarios open. `medium`
costs ~0.2s (p50 0.72s vs 0.53s, p90 0.98s vs 0.80s), so the patient runs
`medium`.

**Referee**, all three scenarios, every case x 3 = 216 runs per config, one
sitting, us-east-1. Accuracy is exact set match on the detected action types;
latency is the whole `chat()` round trip.

| Model / effort | s1 | s2 | s3 | Total | Median | p95 | Max |
|---|---|---|---|---|---|---|---|
| gpt-oss-120b / `low` (mantle) | 62/69 | 56/72 | 65/75 | 183/216 | 0.68–0.96s | ≤2.25s | 5.83s |
| **GPT-6 Luna / `low` (deployed)** | **66/69** | **61/72** | **71/75** | **198/216** | **0.57–0.72s** | **≤1.19s** | **1.36s** |
| GPT-6 Luna / `medium` | 67/69 | — | — | — | 0.61–0.81s | ≤1.72s | 2.33s |

(`medium` was run on an earlier draft of the scenario_2/3 cases — 193/216 there
against `low`'s 195/216 on the same draft — so only its scenario_1 row is
comparable. Zero parse errors in every config: the prompt alone holds the JSON
shape.)

What separates them is not the count but the failure shape. **gpt-oss fired the
game-ending security action on lines that never asked for it** — six times
across the two sittings, including on "I'm really sorry about the wait and the
mix-up. Let me put the lab order in right now…", which would have ended a good
student's game as a fail — and once returned a misspelt action type. Its misses
are scattered (a case passes twice and fails the third time). **Luna never
falsely fired an END action**, and its misses are consistent, 3/3 on the same
cases, which makes them readable:

- An extra `Authoritative` on firm lines whose desc is something else ("Those
  monitor leads are supposed to stay on…"). Defensible, and the stricter reading.
- `Redirect to medical history` on a history question asked after the wait was
  dealt with — the referee sees one utterance, not the conversation (rule 6).
  The action is worth 0 points.
- A missed `Authoritative` on a *conditional* security threat ("If you keep
  raising your voice, we may have to get security involved") — under-charging,
  the direction rule 4 deliberately leans.

The `low` vs `medium` choice for the referee is latency: no accuracy gain for a
p95 half again as long, on the serial critical path.

### History

The prompt rules above were tuned on gpt-oss and on OpenRouter's
`anthropic/claude-haiku-4.5` (68/69 on scenario_1). gpt-oss's own recall gap —
dropping `Authoritative tone` when `Restraint` or `Force IV` sits in the same
utterance, at every effort level — does not reproduce on Luna, which passes both
of those cases 3/3.
