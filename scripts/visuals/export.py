#!/usr/bin/env python3
"""Export web assets from the full-resolution masters — re-runnable, $0.

Usage:
    export.py <scenario_id> [--aspect 1196:880] [--width 2048] [--format webp]
              [--quality 85] [--offset X,Y] [--out DIR]

Writes ``web/public/visuals/<scenario_id>/``:

    environment.<ext>   the plate (opaque)
    patient_<n>.<ext>   full-frame RGBA patient layer per level 0..max_level
    intro.<ext>         plate + the start-level layer, composited

Every output is the same crop of the plate's frame, so the layers stay
pixel-registered with the backdrop. Changing the stage framing later is a
re-run with new flags; nothing here costs API money.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import masters
import matting
import numpy as np
from common import (
    REPO_ROOT,
    layer_key,
    load_rgb,
    load_rgba,
    load_shot_list,
    local_path,
    parse_aspect,
)
from PIL import Image

FORMATS = ("webp", "png")


def _resize(img: np.ndarray, size: tuple[int, int]) -> Image.Image:
    pil = Image.fromarray(img)
    if pil.mode == "RGBA":
        # Resample premultiplied, or transparent pixels' colour bleeds into edges.
        return (
            pil.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")
        )
    return pil.resize(size, Image.Resampling.LANCZOS)


def _save(img: Image.Image, path: Path, fmt: str, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "webp":
        img.save(path, "WEBP", quality=quality, method=6, alpha_quality=100)
    else:
        img.save(path, "PNG", optimize=True)


def safe_area_kept(
    shape: tuple[int, ...],
    aspect: float,
    offset: tuple[float, float],
    safe_area: tuple[float, float, float, float],
) -> bool:
    h, w = shape[:2]
    x0, y0, x1, y1 = matting.crop_box(shape, aspect, offset)
    sx0, sy0, sx1, sy1 = safe_area
    return x0 <= sx0 * w and y0 <= sy0 * h and x1 >= sx1 * w and y1 >= sy1 * h


def export_assets(
    plate: np.ndarray,
    layers: dict[int, np.ndarray],
    start_level: int,
    out_dir: Path,
    *,
    aspect: float = 1196 / 880,
    width: int = 2048,
    fmt: str = "webp",
    quality: int = 85,
    offset: tuple[float, float] = (0.5, 0.5),
) -> list[Path]:
    if fmt not in FORMATS:
        raise SystemExit(f"--format must be one of {FORMATS}")
    size = (width, round(width / aspect))
    written = []

    def emit(name: str, img: np.ndarray) -> None:
        if img.shape[:2] != plate.shape[:2]:
            raise SystemExit(
                f"{name}: {img.shape[1]}x{img.shape[0]} is not the plate's frame"
            )
        path = out_dir / f"{name}.{fmt}"
        _save(
            _resize(matting.crop_to_aspect(img, aspect, offset), size),
            path,
            fmt,
            quality,
        )
        written.append(path)

    emit("environment", plate)
    for level in sorted(layers):
        emit(f"patient_{level}", layers[level])
    if start_level in layers:
        emit("intro", matting.composite_over(layers[start_level], plate))
    return written


def _parse_offset(value: str) -> tuple[float, float]:
    x, y = value.split(",", 1)
    return float(x), float(y)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scenario_id")
    parser.add_argument(
        "--aspect", default="1196:880", help="stage aspect, W:H (default 1196:880)"
    )
    parser.add_argument(
        "--width", type=int, default=2048, help="output width px (default 2048)"
    )
    parser.add_argument("--format", choices=FORMATS, default="webp")
    parser.add_argument("--quality", type=int, default=85, help="lossy quality (webp)")
    parser.add_argument("--offset", type=_parse_offset, help="crop offset X,Y in [0,1]")
    parser.add_argument(
        "--out", type=Path, help="default web/public/visuals/<scenario_id>/"
    )
    parser.add_argument(
        "--allow-missing", action="store_true", help="export the layers present"
    )
    args = parser.parse_args(argv)

    sid = args.scenario_id
    shot = load_shot_list(sid)
    manifest = masters.load_manifest(sid)
    plate_key = manifest["accepted"].get("plate")
    if not plate_key:
        raise SystemExit(f"{sid}: no accepted plate")
    plate = load_rgb(local_path(plate_key))

    layers, missing = {}, []
    for level in range(shot.max_level + 1):
        path = local_path(layer_key(sid, level))
        if path.exists():
            layers[level] = load_rgba(path)
        else:
            missing.append(level)
    if missing and not args.allow_missing:
        raise SystemExit(
            f"{sid}: no extracted layer for level(s) {missing} — run extract.py"
        )

    aspect = parse_aspect(args.aspect)
    offset = args.offset or shot.crop_offset
    if not safe_area_kept(plate.shape, aspect, offset, shot.safe_area):
        print(
            f"warning: the {args.aspect} crop at offset {offset} cuts into the safe_area"
        )
    out = args.out or REPO_ROOT / "web" / "public" / "visuals" / sid
    written = export_assets(
        plate,
        layers,
        shot.start_level,
        out,
        aspect=aspect,
        width=args.width,
        fmt=args.format,
        quality=args.quality,
        offset=offset,
    )
    total = sum(p.stat().st_size for p in written)
    print(f"wrote {len(written)} file(s) to {out} ({total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
