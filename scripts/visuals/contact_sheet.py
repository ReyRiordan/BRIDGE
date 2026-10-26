#!/usr/bin/env python3
"""Build the local review page for a scenario's candidates.

Usage:
    contact_sheet.py <scenario_id>   →  .visuals-work/<scenario_id>/contact_sheet.html

Per slot (the plate, each action layer, then every level): the accepted
candidate; its extracted layer composited as the stage will show it (a level
over the start state — the plate plus the ``start_visible`` layers — and an
action layer over the bare plate) and its alpha on a checkerboard; the runners-up beside it; and rejected candidates
with their notes (``generate.py reject``). The stage crop and the shot list's
``safe_area`` are outlined on each accepted frame. Previews are cached JPEG/PNG
downscales under ``previews/`` — the masters are never touched.
"""

from __future__ import annotations

import argparse
import html
from datetime import datetime
from pathlib import Path

import masters
import matting
import numpy as np
from common import (
    STAGE_ASPECT,
    ShotList,
    action_layer_key,
    load_rgb,
    load_rgba,
    load_shot_list,
    work_root,
)
from PIL import Image

PREVIEW_WIDTH = 1024

CSS = """
:root { color-scheme: light dark; --bg: #f4f4f2; --fg: #1b1b1b; --card: #fff;
  --muted: #6b6b6b; --line: #d9d9d4; --ok: #1f7a3a; --warn: #9a6200; --bad: #a32020; }
@media (prefers-color-scheme: dark) { :root { --bg: #161616; --fg: #ececec;
  --card: #222; --muted: #9a9a9a; --line: #3a3a3a; --ok: #5cc57d; --warn: #e0a640;
  --bad: #ef6b6b; } }
body { margin: 0; padding: 16px 24px 64px; background: var(--bg); color: var(--fg);
  font: 14px/1.4 system-ui, sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 0; }
.meta { color: var(--muted); margin-bottom: 20px; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px 16px; margin-bottom: 16px; }
.head { display: flex; gap: 10px; align-items: baseline; margin-bottom: 4px; }
.chip { font-size: 12px; padding: 1px 8px; border-radius: 99px; border: 1px solid; }
.accepted { color: var(--ok); } .review { color: var(--warn); } .none { color: var(--muted); }
.pose { color: var(--muted); margin-bottom: 10px; }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
figure { margin: 0; } figcaption { font-size: 12px; color: var(--muted);
  word-break: break-all; max-width: 420px; }
.big img, .big .frame { width: 420px; } .small img, .small .frame { width: 220px; }
.frame { position: relative; display: inline-block; line-height: 0; }
.frame img { display: block; }
.box { position: absolute; pointer-events: none; }
.crop { outline: 2px solid rgba(255, 60, 60, .9); }
.safe { outline: 1px dashed rgba(60, 200, 255, .95); }
.checker { background: repeating-conic-gradient(#bbb 0 25%, #eee 0 50%) 0 0 / 16px 16px; }
.rejected { opacity: .55; } .note { color: var(--bad); }
"""


def _preview(src: Path, dest: Path, mode: str = "RGB") -> Path:
    """Cached downscale of ``src`` (JPEG for RGB, PNG for RGBA)."""
    if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        img = img.convert(mode)
        img.thumbnail((PREVIEW_WIDTH, PREVIEW_WIDTH * 2), Image.Resampling.LANCZOS)
        img.save(dest, "PNG" if mode == "RGBA" else "JPEG", quality=85)
    return dest


def _over_plate(layers: list[Path], plate: Path, dest: Path) -> Path:
    """``layers`` composited bottom-to-top over the plate, as a cached preview."""
    newest = max(p.stat().st_mtime for p in [plate, *layers])
    if dest.exists() and dest.stat().st_mtime >= newest:
        return dest
    out = load_rgb(plate)
    for layer in layers:
        out = matting.composite_over(load_rgba(layer), out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".full.png")
    Image.fromarray(np.ascontiguousarray(out)).save(tmp)
    _preview(tmp, dest)
    tmp.unlink()
    return dest


def _overlay(shot: ShotList, size: tuple[int, int], offset: tuple[float, float]) -> str:
    w, h = size
    x0, y0, x1, y1 = matting.crop_box((h, w), STAGE_ASPECT, offset)
    boxes = [("crop", x0 / w, y0 / h, x1 / w, y1 / h), ("safe", *shot.safe_area)]
    return "".join(
        f'<div class="box {cls}" style="left:{a:.2%};top:{b:.2%};'
        f'width:{c - a:.2%};height:{d - b:.2%}"></div>'
        for cls, a, b, c, d in boxes
    )


class Sheet:
    def __init__(self, shot: ShotList, manifest: dict, root: Path):
        self.shot, self.manifest, self.root = shot, manifest, root
        self.base = root / shot.scenario_id
        self.previews = self.base / "previews"

    def rel(self, path: Path) -> str:
        return html.escape(path.relative_to(self.base).as_posix())

    def figure(self, key: str, size: str, overlay: bool = False) -> str:
        path = self.root / key
        meta = self.manifest["objects"].get(key, {})
        name = Path(key).name
        if not path.exists():
            return f'<figure class="{size}"><figcaption>{html.escape(name)} — not in the work dir (masters.py pull)</figcaption></figure>'
        prev = _preview(
            path,
            self.previews
            / Path(key).relative_to(self.shot.scenario_id).with_suffix(".jpg"),
        )
        boxes = ""
        if overlay and meta.get("size"):
            boxes = _overlay(self.shot, tuple(meta["size"]), self.shot.crop_offset)
        cost = f" · ${meta['cost_usd']:.3f}" if "cost_usd" in meta else ""
        cls = f"{size} rejected" if meta.get("rejected") else size
        note = (
            f'<br><span class="note">{html.escape(meta["note"])}</span>'
            if meta.get("note")
            else ""
        )
        return (
            f'<figure class="{cls}"><span class="frame"><img loading="lazy" src="{self.rel(prev)}">{boxes}</span>'
            f"<figcaption>{html.escape(name)}{cost}{note}</figcaption></figure>"
        )

    def layer_figures(self, name: str, below: list[Path], caption: str) -> str:
        """The extracted layer ``name`` over the plate (+ ``below``), and its alpha."""
        layer = self.root / self.shot.scenario_id / "layers" / f"{name}.png"
        plate_key = self.manifest["accepted"].get("plate")
        if not layer.exists() or not plate_key or not (self.root / plate_key).exists():
            return '<figure class="big"><figcaption>no extracted layer yet (extract.py)</figcaption></figure>'
        over = _over_plate(
            [*below, layer],
            self.root / plate_key,
            self.previews / "layers" / f"over_{name}.jpg",
        )
        alpha = _preview(layer, self.previews / "layers" / f"{name}.png", mode="RGBA")
        return (
            f'<figure class="big"><span class="frame"><img src="{self.rel(over)}"></span>'
            f"<figcaption>{html.escape(caption)}</figcaption></figure>"
            f'<figure class="big"><span class="frame checker"><img src="{self.rel(alpha)}"></span>'
            f"<figcaption>extracted alpha</figcaption></figure>"
        )

    def start_state(self) -> list[Path]:
        """The extracted ``start_visible`` layers, bottom to top."""
        sid = self.shot.scenario_id
        paths = [self.root / action_layer_key(sid, n) for n in self.shot.start_visible]
        return [p for p in paths if p.exists()]

    def section(
        self, slot: str, title: str, subtitle: str, prefix: str, layer: str | None
    ) -> str:
        accepted = self.manifest["accepted"].get(slot)
        candidates = sorted(k for k in self.manifest["objects"] if k.startswith(prefix))
        others = [k for k in candidates if k != accepted]
        live = [k for k in others if not self.manifest["objects"][k].get("rejected")]
        dead = [k for k in others if self.manifest["objects"][k].get("rejected")]
        if accepted:
            chip = '<span class="chip accepted">accepted</span>'
        elif candidates:
            chip = f'<span class="chip review">{len(live)} awaiting review</span>'
        else:
            chip = '<span class="chip none">not generated</span>'
        parts = [
            f'<section id="{slot}"><div class="head"><h2>{html.escape(title)}</h2>{chip}</div>'
        ]
        if subtitle:
            parts.append(f'<div class="pose">{html.escape(subtitle)}</div>')
        row = []
        if accepted:
            row.append(self.figure(accepted, "big", overlay=True))
            if layer and slot.startswith("level_"):
                row.append(self.layer_figures(layer, self.start_state(), "start state"))
            elif layer:
                row.append(self.layer_figures(layer, [], "layer over the plate"))
        row += [self.figure(k, "small" if accepted else "big") for k in live]
        parts.append(f'<div class="row">{"".join(row)}</div>')
        if dead:
            parts.append('<div class="pose">rejected</div>')
            parts.append(
                f'<div class="row">{"".join(self.figure(k, "small") for k in dead)}</div>'
            )
        parts.append("</section>")
        return "".join(parts)

    def render(self) -> str:
        sid = self.shot.scenario_id
        spend = masters.spend(self.manifest)
        body = [
            f"<h1>{html.escape(sid)} — contact sheet</h1>",
            f'<div class="meta">built {datetime.now():%Y-%m-%d %H:%M} · spend ${spend["total_usd"]:.2f} '
            f"(${spend['accepted_usd']:.2f} in accepted masters) · red = stage crop, "
            f"dashed blue = safe_area</div>",
            self.section(
                "plate", "Plate", self.shot.room_prompt, f"{sid}/plate/", None
            ),
        ]
        for name, prompt in self.shot.layers.items():
            tag = " (start state)" if name in self.shot.start_visible else ""
            body.append(
                self.section(
                    name,
                    f"Layer {name}{tag}",
                    prompt,
                    f"{sid}/composites/{name}/",
                    name,
                )
            )
        for level in range(self.shot.max_level + 1):
            tag = " (anchor)" if level == self.shot.start_level else ""
            body.append(
                self.section(
                    f"level_{level}",
                    f"Level {level}{tag}",
                    self.shot.frames[level],
                    f"{sid}/composites/level_{level}/",
                    f"patient_{level}",
                )
            )
        return (
            f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(sid)} contact sheet</title><style>{CSS}</style></head>"
            f"<body>{''.join(body)}</body></html>\n"
        )


def build(scenario_id: str, root: Path | None = None) -> Path:
    root = root or work_root()
    sheet = Sheet(load_shot_list(scenario_id), masters.load_manifest(scenario_id), root)
    out = root / scenario_id / "contact_sheet.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sheet.render())
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scenario_id")
    args = parser.parse_args(argv)
    print(build(args.scenario_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
