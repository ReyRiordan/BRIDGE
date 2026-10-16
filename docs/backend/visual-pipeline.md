# Visual pipeline (`scripts/visuals/`)

Agent-driven tooling that produces a scenario's scene art: one photoreal empty-room **plate** plus one full-frame, pixel-registered, transparent **patient layer** per escalation level. It is tooling only. It never runs in CI against a model or the network, and nothing in an image depends on it.

**The method is clean-plate extraction.** The empty room is generated once. The model edits the patient into that room, and the layer's alpha is derived locally by comparing each edit against the plate. Furniture the patient sits on matches the plate, so it stays in the backdrop with no prompt work. The stage always shows the original plate, never a model re-render.

**Masters are full resolution; export is a separate step.** Candidates are generated at the model's largest tier (4K) at 4:3, and matted uncropped. `export.py` derives the web assets, so a new stage framing is a $0 re-run.

## Files

| File | Role |
|---|---|
| `common.py` | Work-dir paths, S3 key layout, shot-list loading, image I/O |
| `matting.py` | Pure extraction functions: crop, ECC alignment, exposure match, diff mask, shading test, trimap, decontamination, shadow alpha, RGBA merge |
| `matte_model.py` | `estimate_alpha`: ViTMatte (lazy torch/transformers) with feathered tiling; BiRefNet (`rembg`) fallback |
| `masters.py` | CLI and module: the S3 bucket (`ensure-bucket` / `push` / `pull` / `status`) and the committed manifest (`record`, `accept`) |
| `generate.py` | CLI: Nano Banana generation (`plate` / `frame` / `chain` / `accept` / `reject` / `collect`) |
| `extract.py` | CLI: accepted composite → full-res RGBA layer master |
| `export.py` | CLI: masters → `web/public/visuals/<scenario_id>/` |
| `contact_sheet.py` | CLI: the local review page |
| `shots/` | Per-scenario shot lists; [`shots/README.md`](../../scripts/visuals/shots/README.md) is the schema, `example.yaml` the reference |
| `masters/<scenario_id>.json` | Committed manifest: provenance without the bytes |
| `requirements.txt` | The tooling's own pins. It is never in an image |

## Setup

```bash
python -m venv .venv-visuals                 # gitignored by .venv*/
.venv-visuals/bin/pip install -r scripts/visuals/requirements.txt
export GEMINI_API_KEY=...                     # generation only
# AWS: the default credential chain (masters bucket only)
```

The first `extract.py` run downloads ViTMatte (`hustvl/vitmatte-base-composition-1k`) from Hugging Face. Torch picks `cuda`, then `mps`, then `cpu`; override it with `--device`.

## End-to-end procedure

Run every command from the repo root as `.venv-visuals/bin/python scripts/visuals/<cli> …`.

1. **Bucket**, once per AWS account: `masters.py ensure-bucket`.
2. **Shot list**: write `scripts/visuals/shots/<scenario_id>.yaml` (schema below).
3. **Plate**: run `generate.py plate <id> -n 3`, review with `contact_sheet.py <id>`, then `generate.py accept <id> plate <file>`. Get sign-off on the room before generating any frame: a plate change invalidates every frame, and `accept` warns when that happens.
4. **Chain**: run `generate.py chain <id>`, review, accept, and repeat.
   - The first run generates the anchor (`start_level`), edited into the plate.
   - Each later run advances both branches by one level (down toward 0, up toward `max_level`).
   - Each level is edited from its accepted neighbour, with the accepted anchor attached as the identity reference.
   - A level with candidates but no acceptance is reported as waiting and is not regenerated.
   - To redo one level, use `generate.py frame <id> <level> [--from <composite>]`.
5. **Review**: run `contact_sheet.py <id>`, which writes `.visuals-work/<id>/contact_sheet.html`.
   - Each slot shows the accepted candidate, with the stage crop (red) and the `safe_area` (dashed blue) outlined.
   - Next to it are its extracted layer over the plate and its alpha on a checkerboard.
   - Runners-up appear alongside. Candidates flagged with `generate.py reject <id> <level> <file> --note "…"` are folded into a "rejected" row with their notes.
   - Claude pre-filters with `reject` before the user sees the sheet.
6. **Extract**: run `extract.py <id> <level|all>`, which writes `.visuals-work/<id>/layers/patient_<n>.png` and records it in the manifest.
7. **Export**: `export.py <id>` (see below).
8. **Push**: `masters.py push <id>`, then commit the manifest and the exported assets.

`--dry-run` on `plate`, `frame` and `chain` prints every request (slot, reference images, prompt) and the estimated standard and batch cost. It needs no API key and does not import the SDK. `chain --dry-run` prints the whole plan in chain order.

## Generation

- **Model**: the default is `gemini-nano-banana-2.1`, and the fallback is `gemini-3.1-flash-image`. The shot list's `model` field and the `--model` flag override it.
- **Tier**: `--size` defaults to the model's largest tier. Every request is `aspect_ratio: "4:3"`.
- **API**: requests go through `generateContent`, which is the only API Gemini Batch accepts, so sync and `--batch` share one request shape.
- **References**: reference images are sent as JPEG, downscaled to 2048 px on the long edge. The model tokenises inputs at a fixed budget, and the downscale keeps batch payloads small.
- **One request per candidate**, because image models return one image.
- **Batch mode**: `--batch` submits a half-price Batch job and polls it. The job record is saved under `.visuals-work/<id>/batches/`. After a Ctrl-C, run `generate.py collect <id> <job>` to resume.
- **Manifest entry**: every saved candidate records its sha256, pixel size, model, tier, prompt, references (`plate` / `source` / `anchor` keys), prompt tokens and cost.

Prices hard-coded in `generate.PRICES` (USD per output image, standard / batch):

| Model | 1K | 2K | 4K |
|---|---|---|---|
| `gemini-nano-banana-2.1` | 0.0336 / 0.0168 | 0.0504 / 0.0252 | 0.113 / 0.0567 |
| `gemini-3.1-flash-image` | 0.067 / 0.034 | 0.101 / 0.050 | 0.151 / 0.076 |

Input images and text add about $0.002 per request.

**Cost per scenario**, first pass at 4K with 3 candidates per slot: a plate plus 11 levels is 36 images, about $4.10 standard or $2.05 batch. Every redo round adds `n × $0.113` per level redone. `masters.py status <id>` and the contact sheet header show the recorded spend.

## Extraction

`extract.py` runs these steps on the full-resolution, uncropped master:

1. **Align**: ECC affine registration (`cv2.findTransformECC`), coarse at ≤1024 px and then refined at full resolution. It is fitted only on pixels outside a changed-region mask, found in two passes (a generous diff, then a tight one). This removes the few-pixel drift and stretch that edits introduce.
2. **Exposure match**: a per-channel gain is fitted on unchanged pixels, so a global tone shift in the edit doesn't read as a diff or a shadow.
3. **Diff mask** (`--threshold`, default 0.1 max per-channel): small specks are dropped and small holes filled.
   - **Shade is excluded.** A pixel counts as shade when it is uniformly darker (all channels by about the same ratio) *and* the plate's texture survives. The texture test is a normalised cross-correlation of high-passed log intensity.
   - This keeps dark clothing on a textured wall in the person mask.
4. **Trimap**: a sure-foreground core and an unknown band (`--erode` / `--dilate`, default long edge / 400 and / 200, so 10 / 20 px at 4K).
5. **Matte**: ViTMatte inside the unknown band.
   - `--tile` (default 1024) runs it only on tiles that touch the band, with feathered overlap.
   - `--matte birefnet` is the fallback. It is not trimap-guided, so its alpha is used only inside the band.
6. **Decontaminate**: `F = (C − (1−α)·B) / α`, which is exact because the background B is the known plate.
7. **Shadow**: shade within a quarter of the frame of the person becomes a black layer with `α = 1 − C/B`, capped at 0.8. It is merged under the person, so the figure doesn't float. `--no-shadow` drops it.

Measured on an Apple M2 (16 GB) with a synthetic 4096×3072 master: `extract.py --tile 1024` takes about 28 s wall, of which matting is 17.5 s on `mps` and alignment 4.4 s, with a 2.2 GB peak RSS. Model load adds about 15 s on first use.

### EC2 matting fallback

If a master is too slow or too large to matte locally, run only the extraction step on a short-lived GPU instance:

1. Launch a `g5.xlarge` (or `g6.xlarge`) from the AWS Deep Learning AMI (PyTorch). Give it an instance role with `s3:GetObject`, `s3:PutObject` and `s3:ListBucket` on the masters bucket.
2. Clone the repo and `pip install -r scripts/visuals/requirements.txt`.
3. Run `masters.py pull <id>`, then `extract.py <id> all --device cuda`, then `masters.py push <id>`.
4. Copy `scripts/visuals/masters/<id>.json` back, for example with `scp`, so the layer entries reach the commit.
5. Terminate the instance. At about $1/h on demand, a scenario's 11 layers take minutes.

## Export

```bash
export.py <id> [--aspect 1196:880] [--width 2048] [--format webp|png] [--quality 85] [--offset X,Y] [--out DIR]
```

Writes `web/public/visuals/<id>/`. Every file is the same crop of the plate's frame, so the layers stay registered with the backdrop.

| File | Content |
|---|---|
| `environment.<ext>` | The plate, opaque (lossy WebP q85 by default) |
| `patient_<n>.<ext>` | Full-frame RGBA layer per level 0..`max_level` (lossy colour with lossless alpha) |
| `intro.<ext>` | The plate with the `start_level` layer composited in |

- **Size**: the defaults give 2048×1507, the stage's 1196:880 aspect.
- **Crop offset**: the crop is centred unless the shot list's `crop_offset` or `--offset` says otherwise. Export warns if the crop cuts into the `safe_area`.
- **Resampling** is premultiplied, so transparent pixels don't bleed colour into the edges.
- **Missing layers**: export refuses if any level has no layer, unless `--allow-missing` is passed.
- **Reframing**: re-running with new flags changes the framing and costs nothing.

## Shot lists

Shot lists live at `scripts/visuals/shots/<id>.yaml`. The required fields are `room_prompt`, `patient_description`, `start_level`, `max_level` and `frames` (`{level: "pose/expression prompt"}`, one entry per level). The optional fields are `safe_area`, `crop_offset` and `model`. The full field table is in [`scripts/visuals/shots/README.md`](../../scripts/visuals/shots/README.md).

`safe_area` is the normalised box that the patient's whole movement path (seat or bed to door) must stay inside. Prompts include it, and export checks it against the crop. A 1196:880 crop of a 4:3 master keeps the full width and about 98% of the height.

## Masters bucket and manifest

- **Bucket**: `bridge-visual-masters-<account-id>` in us-east-1.
  - Block Public Access, versioning and SSE-S3 are on.
  - It is created by `ensure-bucket`, not Amplify, because it is art storage, not app infra.
  - `BRIDGE_VISUALS_BUCKET` overrides the name.
- **Keys** are identical locally (under `.visuals-work/`, overridden by `BRIDGE_VISUALS_WORK`) and in S3:

  ```
  <id>/plate/<candidate>                 every plate candidate
  <id>/composites/level_<n>/<candidate>  every raw frame candidate
  <id>/layers/patient_<n>.png            accepted full-res RGBA masters
  ```

- **push** uploads only files whose sha256 (kept in object metadata) differs. **pull** downloads what the work dir lacks. Previews, contact sheets and batch records stay local.
- **Manifest**: `scripts/visuals/masters/<id>.json` is committed. It holds:
  - `accepted`: slot → key, for `plate` and `level_<n>`
  - `objects`: key → sha256, `size`, bytes, model, tier, prompt, references, cost, and `rejected`/`note`. Layers also record their source composite, plate and matting parameters.

  `BRIDGE_VISUALS_MANIFESTS` redirects it for scratch runs.
- **The work dir** `.visuals-work/` is gitignored. It is a cache. S3 is the source of truth.

## Tests

```bash
pytest scripts/visuals/tests/
ruff check scripts/visuals && ruff format --check scripts/visuals
```

The tests are model-free and network-free, and run in the `visuals` job of `backend-tests.yml`. That job installs only numpy, opencv-python-headless, pillow, pyyaml and boto3. No module imports torch, transformers or `google-genai` at module level, and a test enforces this.

What the tests cover:

- **Synthetic 4:3 scene** (`tests/synth.py`): a textured plate, chair, soft-edged person, cast shadow and a known affine drift.
  - Alignment recovers the drift, and the diff mask finds the person but not the furniture or shadow.
  - The trimap band covers the soft edge, and decontamination recovers the foreground colour.
  - The shadow gets low alpha, and a global gain is undone.
  - The full layer recomposes onto the plate.
- **ViTMatte tiling**, with a fake predictor.
- **Export** sizes, alpha and crop offset.
- **Generation**: the dry-run plan and order, anchor attachment, cost, chain gating, and a stubbed live run.
- **Manifest** round trip and S3 calls, via `botocore.stub.Stubber`.
- **Contact sheet** content.
