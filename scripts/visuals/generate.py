#!/usr/bin/env python3
"""Generate plate and patient-frame candidates with Nano Banana (Gemini API).

Usage:
    generate.py plate  <scenario_id> [-n 3]
    generate.py frame  <scenario_id> <level> [-n 3] [--from <composite>]
    generate.py chain  <scenario_id> [-n 3]
    generate.py accept <scenario_id> <level|plate> <candidate>
    generate.py collect <scenario_id> <batch-job-name>

Common flags: --model, --size, --batch, --dry-run (print the requests and the
estimated cost; no API key, no SDK import, no network).

Consistency: the anchor frame (the shot list's ``start_level``) is edited
into the accepted plate; every other level is edited from its accepted
neighbour toward the anchor (start→0, start→max), with the accepted anchor
always attached as the identity reference. ``chain`` advances each branch by
one level and then stops for review — nothing proceeds past an unaccepted
candidate.

Reads ``GEMINI_API_KEY``. Every candidate is recorded in the manifest with its
model, prompt, references, resolution tier and cost.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import masters
from common import (
    ShotList,
    composite_key,
    load_shot_list,
    local_path,
    plate_key,
    work_root,
)

DEFAULT_MODEL = "gemini-nano-banana-2.1"
FALLBACK_MODEL = "gemini-3.1-flash-image"
ASPECT = "4:3"

# USD per output image by resolution tier: (standard, batch); input $/1M tokens.
PRICES: dict[str, dict] = {
    "gemini-nano-banana-2.1": {
        "output": {
            "1K": (0.0336, 0.0168),
            "2K": (0.0504, 0.0252),
            "4K": (0.113, 0.0567),
        },
        "input_per_m": (1.50, 0.75),
    },
    "gemini-3.1-flash-image": {
        "output": {
            "512": (0.045, 0.022),
            "1K": (0.067, 0.034),
            "2K": (0.101, 0.050),
            "4K": (0.151, 0.076),
        },
        "input_per_m": (0.50, 0.25),
    },
}
TIER_ORDER = ("512", "1K", "2K", "4K")
# Estimate only (the real count comes back in usage_metadata).
IMAGE_INPUT_TOKENS = 1290
# Reference images are downscaled before upload: the model tokenises inputs at
# a fixed budget, and it keeps batch payloads well under the inline limit.
REF_MAX_EDGE = 2048
POLL_SECONDS = 30

PLATE_PROMPT = """\
{room}

Photorealistic photograph, eye-level camera, natural indoor lighting, sharp \
focus throughout. The room is completely empty: no people, no hands, no \
silhouettes, no reflections of people. {aspect} landscape frame."""

ANCHOR_PROMPT = """\
The image is an empty room. Edit it: place the patient described below into \
this exact room.

Patient: {patient}
Pose and expression: {pose}

Keep the camera, framing, perspective, lighting and every object in the room \
exactly as they are; change only the pixels the patient covers and the soft \
shadow they cast. Keep the whole patient inside the box from {x0:.0%} to \
{x1:.0%} of the width and {y0:.0%} to {y1:.0%} of the height. Photorealistic, \
consistent with the room's lighting."""

FRAME_PROMPT = """\
Image 1 is the current scene. Image 2 is the identity reference: the same \
patient — keep their face, hair, skin, build and clothing identical to it.

Edit image 1: change only the patient's pose and expression to: {pose}

Patient: {patient}

Keep the camera, framing, perspective, lighting and every object in the room \
exactly as in image 1; change only the pixels the patient covers and the soft \
shadow they cast. Keep the whole patient inside the box from {x0:.0%} to \
{x1:.0%} of the width and {y0:.0%} to {y1:.0%} of the height."""


@dataclass
class Ref:
    role: str  # "plate" | "source" | "anchor"
    key: str  # S3 key, or "<accepted level_n>" while it doesn't exist yet

    @property
    def resolved(self) -> bool:
        return not self.key.startswith("<")


@dataclass
class Request:
    slot: str  # "plate" | "level_<n>"
    prompt: str
    refs: list[Ref] = field(default_factory=list)
    n: int = 3
    level: int | None = None

    @property
    def is_anchor(self) -> bool:
        return self.level is not None and not any(r.role == "anchor" for r in self.refs)


# --- planning (pure) ----------------------------------------------------------


def _slot(level: int) -> str:
    return f"level_{level}"


def _accepted_or_placeholder(manifest: dict, slot: str) -> str:
    return manifest["accepted"].get(slot) or f"<accepted {slot}>"


def plan_plate(shot: ShotList, n: int) -> Request:
    prompt = PLATE_PROMPT.format(room=shot.room_prompt, aspect=ASPECT)
    return Request(slot="plate", prompt=prompt, n=n)


def plan_frame(
    shot: ShotList, level: int, n: int, manifest: dict, from_key: str | None = None
) -> Request:
    if level not in shot.frames:
        raise SystemExit(f"level {level} is not in the shot list (0..{shot.max_level})")
    x0, y0, x1, y1 = shot.safe_area
    box = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    source = shot.source_level(level)
    if source is None:
        src = from_key or _accepted_or_placeholder(manifest, "plate")
        prompt = ANCHOR_PROMPT.format(
            patient=shot.patient_description, pose=shot.frames[level], **box
        )
        return Request(_slot(level), prompt, [Ref("plate", src)], n, level)
    src = from_key or _accepted_or_placeholder(manifest, _slot(source))
    anchor = _accepted_or_placeholder(manifest, _slot(shot.start_level))
    prompt = FRAME_PROMPT.format(
        patient=shot.patient_description, pose=shot.frames[level], **box
    )
    return Request(
        _slot(level), prompt, [Ref("source", src), Ref("anchor", anchor)], n, level
    )


def plan_chain(shot: ShotList, n: int, manifest: dict) -> list[Request]:
    """Every frame, in chain order — the full plan ``--dry-run`` prints."""
    return [plan_frame(shot, level, n, manifest) for level in shot.chain_order()]


def _has_candidates(manifest: dict, scenario_id: str, slot: str) -> bool:
    prefix = _slot_prefix(scenario_id, slot)
    return any(k.startswith(prefix) for k in manifest["objects"])


def next_chain_steps(
    shot: ShotList, n: int, manifest: dict
) -> tuple[list[Request], list[str]]:
    """What ``chain`` runs now, and what it is waiting on.

    The anchor first; once accepted, the next unaccepted level on each branch
    (downward and upward) whose source is accepted. A level that already has
    candidates but no acceptance is awaiting review, not regenerated.
    """
    accepted = manifest["accepted"]
    if "plate" not in accepted:
        return [], ["plate (generate.py plate, then accept)"]
    start = shot.start_level
    branches = [[start]]
    if start in shot.frames and _slot(start) in accepted:
        branches = [
            list(range(start - 1, -1, -1)),
            list(range(start + 1, shot.max_level + 1)),
        ]
    todo, waiting = [], []
    for branch in branches:
        for level in branch:
            slot = _slot(level)
            if slot in accepted:
                continue
            if _has_candidates(manifest, shot.scenario_id, slot):
                waiting.append(f"{slot} (review candidates, then accept)")
            else:
                todo.append(plan_frame(shot, level, n, manifest))
            break
    return todo, waiting


def price(model: str, tier: str, batch: bool) -> float:
    try:
        return PRICES[model]["output"][tier][1 if batch else 0]
    except KeyError:
        raise SystemExit(
            f"no price for {model} at {tier}; known: {sorted(PRICES)}"
        ) from None


def estimate_cost(requests: list[Request], model: str, tier: str, batch: bool) -> float:
    per_image = price(model, tier, batch)
    per_token = PRICES[model]["input_per_m"][1 if batch else 0] / 1e6
    total = 0.0
    for r in requests:
        tokens = len(r.refs) * IMAGE_INPUT_TOKENS + len(r.prompt) // 4
        total += r.n * (per_image + tokens * per_token)
    return total


def largest_tier(model: str) -> str:
    tiers = PRICES.get(model, {}).get("output", {"4K": None})
    return max(tiers, key=TIER_ORDER.index)


def format_plan(requests: list[Request], model: str, tier: str, batch: bool) -> str:
    mode = "batch" if batch else "standard"
    lines = [f"model {model} · {tier} · {ASPECT} · {mode}"]
    for i, r in enumerate(requests, 1):
        tag = " (anchor)" if r.is_anchor else ""
        refs = ", ".join(f"{ref.role}={ref.key}" for ref in r.refs) or "none"
        first = r.prompt.strip().splitlines()[0]
        lines.append(f"[{i:>2}] {r.slot}{tag} ×{r.n}  refs: {refs}")
        lines.append(f"     prompt: {first[:96]}{'…' if len(first) > 96 else ''}")
    images = sum(r.n for r in requests)
    std = estimate_cost(requests, model, tier, False)
    bat = estimate_cost(requests, model, tier, True)
    lines.append(
        f"{len(requests)} request(s), {images} image(s) · est. ${std:.2f} standard / ${bat:.2f} batch"
    )
    return "\n".join(lines)


# --- execution (network) ------------------------------------------------------


def _client():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set (use --dry-run to plan without it)")
    from google import genai

    return genai.Client(api_key=key)


def _ref_bytes(key: str) -> bytes:
    from PIL import Image

    path = local_path(key)
    if not path.exists():
        raise SystemExit(f"{key} is not in the work dir — masters.py pull first?")
    img = Image.open(path).convert("RGB")
    img.thumbnail((REF_MAX_EDGE, REF_MAX_EDGE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _contents(req: Request) -> list:
    from google.genai import types

    for ref in req.refs:
        if not ref.resolved:
            raise SystemExit(f"{req.slot}: needs {ref.key} first")
    parts = [
        types.Part.from_bytes(data=_ref_bytes(r.key), mime_type="image/jpeg")
        for r in req.refs
    ]
    parts.append(types.Part.from_text(text=req.prompt))
    return [types.Content(role="user", parts=parts)]


def _config(tier: str) -> dict:
    return {
        "response_modalities": ["IMAGE"],
        "image_config": {"aspect_ratio": ASPECT, "image_size": tier},
    }


def _images(response) -> list[tuple[bytes, str]]:
    out = []
    for cand in response.candidates or []:
        for part in (cand.content.parts if cand.content else None) or []:
            if part.inline_data and part.inline_data.data:
                out.append(
                    (part.inline_data.data, part.inline_data.mime_type or "image/png")
                )
    return out


def _candidate_key(
    scenario_id: str, req: Request, stamp: str, i: int, mime: str
) -> str:
    ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    name = f"{req.slot}_{stamp}_{i}.{ext}"
    if req.slot == "plate":
        return plate_key(scenario_id, name)
    return composite_key(scenario_id, req.level, name)


def _save(
    scenario_id: str,
    req: Request,
    images: list[tuple[bytes, str]],
    model: str,
    tier: str,
    batch: bool,
    prompt_tokens: int | None,
    stamp: str,
    start_index: int = 0,
) -> list[str]:
    keys = []
    input_rate = PRICES[model]["input_per_m"][1 if batch else 0] / 1e6
    for i, (data, mime) in enumerate(images, start_index):
        key = _candidate_key(scenario_id, req, stamp, i, mime)
        path = local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        cost = price(model, tier, batch) + (prompt_tokens or 0) * input_rate
        masters.record(
            scenario_id,
            key,
            {
                **masters.describe_file(path),
                "model": model,
                "resolution_tier": tier,
                "aspect_ratio": ASPECT,
                "batch": batch,
                "prompt": req.prompt,
                "references": {r.role: r.key for r in req.refs},
                "prompt_tokens": prompt_tokens,
                "cost_usd": round(cost, 4),
            },
        )
        keys.append(key)
        print(f"  {key}")
    return keys


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def run_sync(
    scenario_id: str, requests: list[Request], model: str, tier: str
) -> list[str]:
    client = _client()
    keys = []
    for req in requests:
        contents = _contents(req)
        stamp = _stamp()
        for i in range(req.n):
            resp = client.models.generate_content(
                model=model, contents=contents, config=_config(tier)
            )
            imgs = _images(resp)
            if not imgs:
                print(
                    f"  {req.slot} #{i}: no image returned ({resp.text or 'no text'})"
                )
                continue
            tokens = (
                resp.usage_metadata.prompt_token_count if resp.usage_metadata else None
            )
            keys += _save(
                scenario_id, req, imgs[:1], model, tier, False, tokens, stamp, i
            )
    return keys


def _batch_state_path(scenario_id: str, job_name: str) -> Path:
    return work_root() / scenario_id / "batches" / f"{job_name.replace('/', '_')}.json"


def run_batch(
    scenario_id: str, requests: list[Request], model: str, tier: str
) -> list[str]:
    client = _client()
    src, index = [], []
    for req in requests:
        contents = _contents(req)
        for i in range(req.n):
            src.append({"contents": contents, "config": _config(tier)})
            index.append({"slot": req.slot, "level": req.level, "i": i})
    job = client.batches.create(
        model=model,
        src=src,
        config={"display_name": f"bridge-{scenario_id}-{_stamp()}"},
    )
    state = {
        "job": job.name,
        "model": model,
        "tier": tier,
        "stamp": _stamp(),
        "index": index,
        "requests": {
            r.slot: {
                "prompt": r.prompt,
                "level": r.level,
                "refs": [[x.role, x.key] for x in r.refs],
            }
            for r in requests
        },
    }
    path = _batch_state_path(scenario_id, job.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))
    print(
        f"submitted {job.name} ({len(src)} image(s)); polling — safe to Ctrl-C and run"
    )
    print(f"  generate.py collect {scenario_id} {job.name}")
    return collect(scenario_id, job.name, client)


def collect(scenario_id: str, job_name: str, client=None) -> list[str]:
    path = _batch_state_path(scenario_id, job_name)
    if not path.exists():
        raise SystemExit(f"no local record of {job_name} under {path.parent}")
    state = json.loads(path.read_text())
    client = client or _client()
    done = {
        "JOB_STATE_SUCCEEDED",
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_EXPIRED",
    }
    job = client.batches.get(name=job_name)
    while job.state.name not in done:
        print(f"  {job.state.name} …")
        time.sleep(POLL_SECONDS)
        job = client.batches.get(name=job_name)
    if job.state.name != "JOB_STATE_SUCCEEDED":
        raise SystemExit(f"{job_name} ended {job.state.name}")
    reqs = {
        slot: Request(
            slot, r["prompt"], [Ref(a, b) for a, b in r["refs"]], 1, r["level"]
        )
        for slot, r in state["requests"].items()
    }
    keys = []
    for meta, item in zip(state["index"], job.dest.inlined_responses):
        if item.error or not item.response:
            print(f"  {meta['slot']} #{meta['i']}: {item.error}")
            continue
        imgs = _images(item.response)
        if not imgs:
            print(f"  {meta['slot']} #{meta['i']}: no image returned")
            continue
        usage = item.response.usage_metadata
        tokens = usage.prompt_token_count if usage else None
        req = reqs[meta["slot"]]
        keys += _save(
            scenario_id,
            req,
            imgs[:1],
            state["model"],
            state["tier"],
            True,
            tokens,
            state["stamp"],
            meta["i"],
        )
    path.rename(path.with_suffix(".done.json"))
    return keys


# --- accept -------------------------------------------------------------------


def _slot_prefix(scenario_id: str, slot: str) -> str:
    return (
        f"{scenario_id}/plate/"
        if slot == "plate"
        else f"{scenario_id}/composites/{slot}/"
    )


def find_key(scenario_id: str, candidate: str, manifest: dict, prefix: str = "") -> str:
    """A candidate given as a key, a work-dir path or a bare file name → its key."""
    objects = [
        k for k in manifest["objects"] if k.startswith(prefix or f"{scenario_id}/")
    ]
    if candidate in objects:
        return candidate
    p = Path(candidate)
    try:
        rel = p.resolve().relative_to(work_root().resolve()).as_posix()
        if rel in objects:
            return rel
    except ValueError:
        pass
    matches = [k for k in objects if Path(k).name == p.name]
    if len(matches) == 1:
        return matches[0]
    where = prefix or f"{scenario_id}/"
    raise SystemExit(f"no unique candidate {candidate!r} under {where} in the manifest")


def accept(scenario_id: str, target: str, candidate: str) -> str:
    slot = "plate" if target == "plate" else _slot(int(target))
    manifest = masters.load_manifest(scenario_id)
    key = find_key(scenario_id, candidate, manifest, _slot_prefix(scenario_id, slot))
    previous = manifest["accepted"].get(slot)
    masters.accept(scenario_id, slot, key)
    if slot == "plate" and previous and previous != key:
        frames = [s for s in manifest["accepted"] if s != "plate"]
        if frames:
            print(
                f"warning: the plate changed — every accepted frame ({', '.join(frames)}) was"
            )
            print("edited into the old plate and must be regenerated.")
    return key


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--model", help=f"image model (default: shot list, else {DEFAULT_MODEL})"
    )
    common.add_argument("--size", help="resolution tier (default: the model's largest)")
    common.add_argument(
        "--batch", action="store_true", help="Gemini Batch API (half price, slow)"
    )
    common.add_argument(
        "--dry-run", action="store_true", help="print the plan and cost; no API"
    )
    common.add_argument(
        "-n", type=int, default=3, help="candidates per frame (default 3)"
    )

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plate", parents=[common], help="empty-room plate candidates")
    p.add_argument("scenario_id")
    p = sub.add_parser("frame", parents=[common], help="candidates for one level")
    p.add_argument("scenario_id")
    p.add_argument("level", type=int)
    p.add_argument(
        "--from", dest="from_key", help="edit from this composite, not the neighbour"
    )
    p = sub.add_parser(
        "chain", parents=[common], help="advance the chain one step per branch"
    )
    p.add_argument("scenario_id")
    p = sub.add_parser("accept", help="accept a candidate for a level or the plate")
    p.add_argument("scenario_id")
    p.add_argument("target", help="a level number, or 'plate'")
    p.add_argument("candidate", help="file name, work-dir path, or S3 key")
    p = sub.add_parser("collect", help="wait for and save a submitted batch job")
    p.add_argument("scenario_id")
    p.add_argument("job")
    args = parser.parse_args(argv)

    if args.cmd == "accept":
        print(f"accepted {accept(args.scenario_id, args.target, args.candidate)}")
        return 0
    if args.cmd == "collect":
        collect(args.scenario_id, args.job)
        return 0

    shot = load_shot_list(args.scenario_id)
    model = args.model or shot.model or DEFAULT_MODEL
    tier = args.size or largest_tier(model)
    price(model, tier, args.batch)  # validates model/tier early
    manifest = masters.load_manifest(args.scenario_id)

    if args.cmd == "plate":
        requests = [plan_plate(shot, args.n)]
    elif args.cmd == "frame":
        from_key = args.from_key and find_key(args.scenario_id, args.from_key, manifest)
        requests = [plan_frame(shot, args.level, args.n, manifest, from_key)]
    else:
        if args.dry_run:
            requests = plan_chain(shot, args.n, manifest)
        else:
            requests, waiting = next_chain_steps(shot, args.n, manifest)
            for w in waiting:
                print(f"waiting on {w}")
            if not requests:
                if not waiting:
                    print("every level is accepted — nothing to generate")
                return 0

    print(format_plan(requests, model, tier, args.batch))
    if args.dry_run:
        return 0
    run = run_batch if args.batch else run_sync
    keys = run(args.scenario_id, requests, model, tier)
    print(
        f"{len(keys)} candidate(s) saved; review with contact_sheet.py {args.scenario_id}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
