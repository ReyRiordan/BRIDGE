"""Shared paths, shot-list loading and image I/O for the visuals pipeline.

Every master lives at the same relative key locally and in S3::

    <scenario_id>/plate/<candidate>.png
    <scenario_id>/composites/level_<n>/<candidate>.png
    <scenario_id>/layers/patient_<n>.png

Locally the key is resolved under the work dir (``.visuals-work/`` at the repo
root, override with ``BRIDGE_VISUALS_WORK``); in S3 it is the object key.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SHOTS_DIR = HERE / "shots"
STAGE_ASPECT = 1196 / 880


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
    return shot


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
