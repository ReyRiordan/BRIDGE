#!/usr/bin/env python3
"""Extract a pixel-registered RGBA layer from an accepted composite.

Usage:
    extract.py <scenario_id> <level|layer|all> [--tile 1024] [--matte vitmatte|birefnet]

Reads the accepted plate and the accepted ``level_<n>`` (or action-layer)
composite from the work dir and writes the full-resolution, uncropped master
``.visuals-work/<scenario_id>/layers/patient_<n>.png`` (or ``<layer>.png``) in
plate coordinates — the app's backdrop is always the original plate — recorded
in the manifest. ``all`` extracts every accepted level and layer.

    align → match exposure → diff mask → trimap → matte → decontaminate
          → shadow → merge
"""

from __future__ import annotations

import argparse
import time

import masters
import matte_model
import matting
import numpy as np
from common import (
    action_layer_key,
    layer_key,
    load_rgb,
    load_shot_list,
    local_path,
    save_png,
)

DEFAULTS = {
    "threshold": 0.1,
    # Trimap band, as a fraction of the long edge (≈10 / 20 px at 4K).
    "erode": 1 / 400,
    "dilate": 1 / 200,
    "tile": 1024,
    # Largest hole (fraction of the frame) filled inside the subject mask.
    "max_hole": 2e-3,
    # Smallest island (fraction of the frame) kept in the subject mask.
    "min_area": 5e-4,
    # Closing radius (fraction of the long edge) that bridges subject gaps the
    # diff can't see — dark legs over a near-black shadow. 0 = off.
    "close": 0.0,
    # Keep only islands within this distance (fraction of the long edge) of
    # the largest one. 0 = keep every island.
    "near": 0.0,
}


def extract_layer(
    plate: np.ndarray,
    composite: np.ndarray,
    *,
    threshold: float = DEFAULTS["threshold"],
    erode_px: int | None = None,
    dilate_px: int | None = None,
    tile: int | None = DEFAULTS["tile"],
    backend: str = "vitmatte",
    device: str | None = None,
    shadow: bool = True,
    relight: bool = False,
    max_hole: float = DEFAULTS["max_hole"],
    min_area: float = DEFAULTS["min_area"],
    close: float = DEFAULTS["close"],
    near: float = DEFAULTS["near"],
    predictor=None,
) -> tuple[np.ndarray, dict]:
    """The full pipeline on in-memory arrays → (RGBA layer, stats).

    ``relight`` carries the edit's smooth lighting changes (brightening as
    well as shade) as a soft tone layer instead of the black shadow — for
    props, whose move both adds and removes shadows on the plate.
    """
    long_edge = max(plate.shape[:2])
    erode_px = (
        erode_px
        if erode_px is not None
        else max(1, round(long_edge * DEFAULTS["erode"]))
    )
    dilate_px = (
        dilate_px
        if dilate_px is not None
        else max(1, round(long_edge * DEFAULTS["dilate"]))
    )
    stats: dict = {}

    t = time.time()
    aligned = matting.align_to_plate(plate, composite)
    changed = matting.diff_mask(plate, aligned, 0.05, ignore_shading=False)
    changed = matting.grow(changed, dilate_px)
    aligned = matting.match_exposure(plate, aligned, exclude=changed)
    stats["align_s"] = round(time.time() - t, 2)

    mask = matting.diff_mask(
        plate,
        aligned,
        threshold,
        min_area=min_area,
        max_hole=max_hole,
        ignore_lighting=relight,
    )
    if close > 0:
        radius = max(1, round(long_edge * close))
        mask = matting.close_mask(mask, radius)
    if near > 0:
        mask = matting.keep_near(mask, max(1, round(long_edge * near)))
    if not mask.any():
        raise SystemExit(
            "the composite matches the plate — nothing changed (lower --threshold?)"
        )
    trimap = matting.make_trimap(mask, erode_px, dilate_px)

    t = time.time()
    alpha = matte_model.estimate_alpha(
        aligned, trimap, tile=tile, backend=backend, device=device, predictor=predictor
    )
    stats["matte_s"] = round(time.time() - t, 2)

    if relight:
        tone_fg, shade = matting.tone_layer(plate, aligned, alpha > 0.5, threshold)
        # Soft edges sit over the tone layer, not the bare plate: decontaminate
        # against what is actually beneath them, or the tone counts twice.
        beneath = matting.composite_over(matting.to_rgba(tone_fg, shade), plate)
        # Blend the edit into what lies beneath across the soft edge, so the
        # subject's outer contour carries no tone step either.
        region = matting.grow(alpha > 0.01, max(2, dilate_px // 4))
        relit = matting.seamless(aligned, beneath, region)
        fg = matting.decontaminate(relit, beneath, alpha)
        fg, out_alpha = matting.merge_layers(fg, alpha, tone_fg, shade)
    else:
        fg = matting.decontaminate(aligned, plate, alpha)
        shade = (
            matting.shadow_alpha(plate, aligned, alpha > 0.5)
            if shadow
            else np.zeros_like(alpha, np.float32)
        )
        fg, out_alpha = matting.merge_shadow(fg, alpha, shade)
    stats.update(
        person_px=int((alpha > 0.5).sum()),
        shadow_px=int((shade > 0).sum()),
        unknown_px=int((trimap == 128).sum()),
        erode_px=erode_px,
        dilate_px=dilate_px,
    )
    return matting.to_rgba(fg, out_alpha), stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scenario_id")
    parser.add_argument(
        "target",
        help="a level number, an action-layer name, or 'all' (everything accepted)",
    )
    parser.add_argument("--threshold", type=float, default=DEFAULTS["threshold"])
    parser.add_argument(
        "--erode", type=int, help="trimap erode px (default: long edge / 400)"
    )
    parser.add_argument(
        "--dilate", type=int, help="trimap dilate px (default: long edge / 200)"
    )
    parser.add_argument(
        "--tile", type=int, default=DEFAULTS["tile"], help="0 = untiled"
    )
    parser.add_argument("--matte", choices=matte_model.BACKENDS, default="vitmatte")
    parser.add_argument("--device", help="torch device (default: cuda > mps > cpu)")
    parser.add_argument("--no-shadow", action="store_true", help="drop the cast shadow")
    parser.add_argument(
        "--max-hole",
        type=float,
        default=DEFAULTS["max_hole"],
        help="largest hole filled in the subject mask, as a fraction of the frame",
    )
    parser.add_argument(
        "--min-area",
        type=float,
        default=DEFAULTS["min_area"],
        help="smallest island kept in the subject mask, as a fraction of the frame",
    )
    parser.add_argument(
        "--close",
        type=float,
        default=DEFAULTS["close"],
        help="closing radius bridging gaps in the subject mask (fraction of the long edge)",
    )
    parser.add_argument(
        "--near",
        type=float,
        default=DEFAULTS["near"],
        help="keep only islands this close to the largest (fraction of the long edge)",
    )
    parser.add_argument(
        "--relight",
        action="store_true",
        help="carry smooth lighting changes (lighter too) as a soft tone layer",
    )
    args = parser.parse_args(argv)

    sid = args.scenario_id
    shot = load_shot_list(sid)
    manifest = masters.load_manifest(sid)
    plate_key = manifest["accepted"].get("plate")
    if not plate_key:
        raise SystemExit(f"{sid}: no accepted plate")
    # (slot, output key) for each target.
    slots = [(name, action_layer_key(sid, name)) for name in shot.layers]
    slots += [(f"level_{lv}", layer_key(sid, lv)) for lv in range(shot.max_level + 1)]
    if args.target == "all":
        targets = [(s, k) for s, k in slots if s in manifest["accepted"]]
    else:
        slot = f"level_{args.target}" if args.target.isdigit() else args.target
        targets = [(s, k) for s, k in slots if s == slot]
        if not targets:
            raise SystemExit(f"{sid}: {args.target!r} is not a level or a layer")
    plate = load_rgb(local_path(plate_key))

    for slot, key in targets:
        src = manifest["accepted"].get(slot)
        if not src:
            raise SystemExit(f"{sid}: {slot} has no accepted composite")
        rgba, stats = extract_layer(
            plate,
            load_rgb(local_path(src)),
            threshold=args.threshold,
            erode_px=args.erode,
            dilate_px=args.dilate,
            tile=args.tile or None,
            backend=args.matte,
            device=args.device,
            shadow=not args.no_shadow,
            relight=args.relight,
            max_hole=args.max_hole,
            min_area=args.min_area,
            close=args.close,
            near=args.near,
        )
        path = local_path(key)
        save_png(rgba, path)
        masters.record(
            sid,
            key,
            {
                **masters.describe_file(path),
                "source": src,
                "plate": plate_key,
                "matte": args.matte,
                "params": {
                    "threshold": args.threshold,
                    "tile": args.tile,
                    "shadow": not args.no_shadow,
                    "relight": args.relight,
                    "max_hole": args.max_hole,
                    "min_area": args.min_area,
                    "close": args.close,
                    "near": args.near,
                    "erode_px": stats["erode_px"],
                    "dilate_px": stats["dilate_px"],
                },
            },
        )
        print(f"{key}  {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
