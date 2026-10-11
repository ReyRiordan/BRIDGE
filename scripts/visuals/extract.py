#!/usr/bin/env python3
"""Extract a pixel-registered RGBA patient layer from an accepted composite.

Usage:
    extract.py <scenario_id> <level|all> [--tile 1024] [--matte vitmatte|birefnet]

Reads the accepted plate and the accepted ``level_<n>`` composite from the
work dir and writes the full-resolution, uncropped master
``.visuals-work/<scenario_id>/layers/patient_<n>.png`` (plate coordinates —
the app's backdrop is always the original plate), recorded in the manifest.

    align → match exposure → diff mask → trimap → matte → decontaminate
          → shadow → merge
"""

from __future__ import annotations

import argparse
import time

import cv2
import masters
import matte_model
import matting
import numpy as np
from common import layer_key, load_rgb, load_shot_list, local_path, save_png

DEFAULTS = {
    "threshold": 0.1,
    # Trimap band, as a fraction of the long edge (≈10 / 20 px at 4K).
    "erode": 1 / 400,
    "dilate": 1 / 200,
    "tile": 1024,
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
    predictor=None,
) -> tuple[np.ndarray, dict]:
    """The full pipeline on in-memory arrays → (RGBA layer, stats)."""
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
    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1,) * 2)
    changed = cv2.dilate(changed.astype(np.uint8), grow) > 0
    aligned = matting.match_exposure(plate, aligned, exclude=changed)
    stats["align_s"] = round(time.time() - t, 2)

    mask = matting.diff_mask(plate, aligned, threshold)
    if not mask.any():
        raise SystemExit(
            "the composite matches the plate — no patient found (lower --threshold?)"
        )
    trimap = matting.make_trimap(mask, erode_px, dilate_px)

    t = time.time()
    alpha = matte_model.estimate_alpha(
        aligned, trimap, tile=tile, backend=backend, device=device, predictor=predictor
    )
    stats["matte_s"] = round(time.time() - t, 2)

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
    parser.add_argument("level", help="a level number, or 'all' (every accepted level)")
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
    args = parser.parse_args(argv)

    sid = args.scenario_id
    shot = load_shot_list(sid)
    manifest = masters.load_manifest(sid)
    plate_key = manifest["accepted"].get("plate")
    if not plate_key:
        raise SystemExit(f"{sid}: no accepted plate")
    if args.level == "all":
        levels = [
            lv
            for lv in range(shot.max_level + 1)
            if f"level_{lv}" in manifest["accepted"]
        ]
    else:
        levels = [int(args.level)]
    plate = load_rgb(local_path(plate_key))

    for level in levels:
        src = manifest["accepted"].get(f"level_{level}")
        if not src:
            raise SystemExit(f"{sid}: level {level} has no accepted composite")
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
        )
        key = layer_key(sid, level)
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
                    "erode_px": stats["erode_px"],
                    "dilate_px": stats["dilate_px"],
                },
            },
        )
        print(f"{key}  {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
