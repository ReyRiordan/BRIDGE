# Shot lists

One YAML per scenario, `<scenario_id>.yaml`, read by `generate.py`, `export.py`
and `contact_sheet.py`. `example.yaml` is the reference and the dry-run test
fixture.

| Field | Required | Meaning |
|---|---|---|
| `room_prompt` | yes | The empty room (the plate). Name the furniture the patient will use and put the door in frame. |
| `patient_description` | yes | Who the patient is — age, build, hair, clothing. Sent with every frame request. |
| `start_level` | yes | The anchor: the first frame generated (from the plate) and the identity reference for every other frame. Also becomes `intro.webp`. Use the scenario's starting escalation. |
| `max_level` | yes | Highest escalation level; frames run `0..max_level`. |
| `frames` | yes | `{<level>: "<pose/expression prompt>"}`, exactly one entry per level `0..max_level`. |
| `safe_area` | no | Normalised `[x0, y0, x1, y1]` box the patient's whole movement path (seat/bed → door) must stay inside, so every frame survives the stage crop. Default: the full frame. |
| `crop_offset` | no | `[x, y]` in `[0, 1]`: where the stage crop sits within the 4:3 master. `[0.5, 0.5]` = centred (default). `export.py --offset` overrides it. |
| `model` | no | Image model ID. Default `gemini-nano-banana-2.1`; fallback `gemini-3.1-flash-image`. `--model` overrides it. |
| `size` | no | Resolution tier (`1K`/`2K`/`4K`). Default: the model's largest. `--size` overrides it. |
| `layers` | no | `{<name>: {prompt: "<edit>"}}`: action layers — third-party or prop scene changes, each one edit of the empty plate (`generate.py layer`), extracted to `<name>.png` and exported as `<name>.webp`. Names are snake_case and must not be `plate`, `environment`, `intro`, `level_<n>` or `patient_<n>`. The app stacks them below the patient. |
| `start_visible` | no | Layer names shown in the start state: composited into `intro.webp` and under every level on the contact sheet. Default: none. |

The 1196:880 stage crop of a 4:3 master keeps the full width and about 98% of
the height (≈1% trimmed from top and bottom when centred), so a `safe_area`
with ≥2% vertical margin is always kept.
