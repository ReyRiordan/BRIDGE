"""Shared paths, shot-list loading and image I/O for the visuals pipeline.

Every master lives at the same relative key locally and in S3::

    <scenario_id>/plate/<candidate>.png
    <scenario_id>/composites/level_<n>/<candidate>.png
    <scenario_id>/composites/<layer>/<candidate>.png   (action layers)
    <scenario_id>/layers/patient_<n>.png
    <scenario_id>/layers/<layer>.png

Locally the key is resolved under the work dir (``.visuals-work/`` at the repo
root, override with ``BRIDGE_VISUALS_WORK``); in S3 it is the object key.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SHOTS_DIR = HERE / "shots"
STAGE_ASPECT = 1196 / 880
# Action-layer names become slot names, key segments and export file names.
LAYER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
RESERVED_NAMES = re.compile(r"^(plate|environment|intro|level_\d+|patient_\d+)$")


def work_root() -> Path:
    return Path(os.environ.get("BRIDGE_VISUALS_WORK", REPO_ROOT / ".visuals-work"))


def local_path(key: str) -> Path:
    return work_root() / key


def plate_key(scenario_id: str, candidate: str) -> str:
    return f"{scenario_id}/plate/{candidate}"


def composite_key(scenario_id: str, level: int, candidate: str) -> str:
    return f"{scenario_id}/composites/level_{level}/{candidate}"


def layer_key(scenario_id: str, level: int) -> str:
    return f"{scenario_id}/layers/patient_{level}.png"


def action_layer_key(scenario_id: str, name: str) -> str:
    return f"{scenario_id}/layers/{name}.png"


def candidate_key(scenario_id: str, slot: str, candidate: str) -> str:
    """Where a raw candidate for ``slot`` (plate, ``level_<n>`` or a layer) lives."""
    if slot == "plate":
        return plate_key(scenario_id, candidate)
    return f"{scenario_id}/composites/{slot}/{candidate}"


def parse_aspect(value: str | float) -> float:
    """``"1196:880"`` / ``"4:3"`` / ``"1.359"`` → float."""
    if isinstance(value, (int, float)):
        return float(value)
    if ":" in value:
        w, h = value.split(":", 1)
        return float(w) / float(h)
    return float(value)


@dataclass
class ShotList:
    scenario_id: str
    room_prompt: str
    patient_description: str
    start_level: int
    max_level: int
    frames: dict[int, str]
    safe_area: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    crop_offset: tuple[float, float] = (0.5, 0.5)
    model: str | None = None
    size: str | None = None
    # Action layers: name → edit prompt, each an independent edit of the plate.
    layers: dict[str, str] = field(default_factory=dict)
    start_visible: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    def chain_order(self) -> list[int]:
        """Anchor first, then outward: start→0, then start→max."""
        down = list(range(self.start_level - 1, -1, -1))
        up = list(range(self.start_level + 1, self.max_level + 1))
        return [self.start_level, *down, *up]

    def source_level(self, level: int) -> int | None:
        """The neighbour a frame is edited from (None = the plate, for the anchor)."""
        if level == self.start_level:
            return None
        return level + 1 if level < self.start_level else level - 1


def load_shot_list(scenario_id: str, shots_dir: Path = SHOTS_DIR) -> ShotList:
    path = shots_dir / f"{scenario_id}.yaml"
    if not path.exists():
        raise SystemExit(f"no shot list at {path}")
    raw = yaml.safe_load(path.read_text())
    known = {
        "room_prompt",
        "patient_description",
        "start_level",
        "max_level",
        "frames",
        "safe_area",
        "crop_offset",
        "model",
        "size",
        "layers",
        "start_visible",
    }
    missing = {
        "room_prompt",
        "patient_description",
        "start_level",
        "max_level",
        "frames",
    } - set(raw)
    if missing:
        raise SystemExit(f"{path}: missing {sorted(missing)}")
    frames = {int(k): str(v).strip() for k, v in raw["frames"].items()}
    shot = ShotList(
        scenario_id=scenario_id,
        room_prompt=str(raw["room_prompt"]).strip(),
        patient_description=str(raw["patient_description"]).strip(),
        start_level=int(raw["start_level"]),
        max_level=int(raw["max_level"]),
        frames=frames,
        safe_area=tuple(float(v) for v in raw.get("safe_area", (0.0, 0.0, 1.0, 1.0))),
        crop_offset=tuple(float(v) for v in raw.get("crop_offset", (0.5, 0.5))),
        model=raw.get("model"),
        size=raw.get("size"),
        layers=_parse_layers(path, raw.get("layers") or {}),
        start_visible=tuple(raw.get("start_visible") or ()),
        extra={k: v for k, v in raw.items() if k not in known},
    )
    expected = set(range(shot.max_level + 1))
    if set(frames) != expected:
        raise SystemExit(
            f"{path}: frames must cover levels 0..{shot.max_level} exactly"
        )
    if not 0 <= shot.start_level <= shot.max_level:
        raise SystemExit(f"{path}: start_level outside 0..max_level")
    x0, y0, x1, y1 = shot.safe_area
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise SystemExit(f"{path}: safe_area must be a normalised [x0, y0, x1, y1] box")
    unknown = [n for n in shot.start_visible if n not in shot.layers]
    if unknown:
        raise SystemExit(f"{path}: start_visible names unknown layer(s) {unknown}")
    return shot


def _parse_layers(path: Path, raw: dict) -> dict[str, str]:
    layers = {}
    for name, spec in raw.items():
        name = str(name)
        if not LAYER_NAME.match(name) or RESERVED_NAMES.match(name):
            raise SystemExit(
                f"{path}: layer name {name!r} must be snake_case and not a reserved"
                " name (plate, environment, intro, level_<n>, patient_<n>)"
            )
        if not isinstance(spec, dict) or not spec.get("prompt"):
            raise SystemExit(f"{path}: layer {name!r} needs a prompt")
        layers[name] = str(spec["prompt"]).strip()
    return layers


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def load_rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))


def save_png(arr: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path, "PNG")
