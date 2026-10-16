"""Soft alpha inside a trimap's unknown band — ViTMatte, lazy-loaded.

torch / transformers are imported only when a model is first used, so the
rest of the tooling (and CI) never needs them. ``tile`` bounds memory on
full-resolution masters: only tiles that contain unknown pixels are run,
with feathered overlap, so a 4K plate costs a few tiles around the person.

Fallback: ``backend="birefnet"`` (BiRefNet via ``rembg``, model
``birefnet-general``, MIT) — not trimap-guided, so it is only used inside the
unknown band. ``pip install rembg`` to enable it.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

import numpy as np

VITMATTE_MODEL = "hustvl/vitmatte-base-composition-1k"
BACKENDS = ("vitmatte", "birefnet")

# (composite HxWx3 uint8, trimap HxW uint8) -> alpha HxW float32 in [0, 1]
Predictor = Callable[[np.ndarray, np.ndarray], np.ndarray]


def _default_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@lru_cache(maxsize=2)
def _vitmatte(model_id: str, device: str | None) -> Predictor:
    import torch
    import torch.nn.functional as F
    from transformers import VitMatteForImageMatting

    device = device or _default_device()
    model = VitMatteForImageMatting.from_pretrained(model_id).to(device).eval()

    def predict(img: np.ndarray, trimap: np.ndarray) -> np.ndarray:
        # The VitMatteImageProcessor recipe, inlined: RGB normalised to
        # [-1, 1], trimap to [0, 1] as a 4th channel, padded to a multiple of 32.
        h, w = trimap.shape
        x = torch.from_numpy(np.ascontiguousarray(img)).permute(2, 0, 1).float() / 255.0
        x = (x - 0.5) / 0.5
        t = torch.from_numpy(np.ascontiguousarray(trimap)).float()[None] / 255.0
        inp = F.pad(torch.cat([x, t])[None], (0, (-w) % 32, 0, (-h) % 32))
        with torch.inference_mode():
            alphas = model(pixel_values=inp.to(device)).alphas
        return alphas[0, 0, :h, :w].float().cpu().numpy()

    return predict


@lru_cache(maxsize=1)
def _birefnet() -> Predictor:
    try:
        from rembg import new_session, remove
    except ImportError as e:  # pragma: no cover - optional dependency
        raise SystemExit("the birefnet fallback needs `pip install rembg`") from e
    from PIL import Image

    session = new_session("birefnet-general")

    def predict(img: np.ndarray, trimap: np.ndarray) -> np.ndarray:
        mask = remove(Image.fromarray(img), session=session, only_mask=True)
        return np.asarray(mask, np.float32) / 255.0

    return predict


def _ramp(n: int, overlap: int, at_start: bool, at_end: bool) -> np.ndarray:
    """1 in the middle, fading over ``overlap`` px at edges shared with a neighbour."""
    r = np.ones(n, np.float32)
    k = min(overlap, n // 2)
    if k > 0:
        fade = np.linspace(1.0 / (k + 1), 1.0, k, dtype=np.float32)
        if not at_start:
            r[:k] = fade
        if not at_end:
            r[n - k :] = fade[::-1]
    return r


def _starts(size: int, tile: int, stride: int) -> list[int]:
    if size <= tile:
        return [0]
    starts = list(range(0, size - tile, stride))
    return [*starts, size - tile]


def tiled(
    predict: Predictor,
    composite: np.ndarray,
    trimap: np.ndarray,
    tile: int,
    overlap: int | None = None,
) -> np.ndarray:
    """Run ``predict`` over overlapping tiles that touch the unknown band."""
    h, w = trimap.shape
    overlap = tile // 8 if overlap is None else overlap
    stride = max(1, tile - overlap)
    acc = np.zeros((h, w), np.float32)
    weight = np.zeros((h, w), np.float32)
    unknown = trimap == 128
    for y in _starts(h, tile, stride):
        for x in _starts(w, tile, stride):
            ys, xs = slice(y, min(y + tile, h)), slice(x, min(x + tile, w))
            if not unknown[ys, xs].any():
                continue
            th, tw = ys.stop - y, xs.stop - x
            win = np.outer(
                _ramp(th, overlap, y == 0, ys.stop == h),
                _ramp(tw, overlap, x == 0, xs.stop == w),
            )
            acc[ys, xs] += predict(composite[ys, xs], trimap[ys, xs]) * win
            weight[ys, xs] += win
    out = np.zeros((h, w), np.float32)
    covered = weight > 0
    out[covered] = acc[covered] / weight[covered]
    return out


def estimate_alpha(
    composite: np.ndarray,
    trimap: np.ndarray,
    tile: int | None = None,
    backend: str = "vitmatte",
    device: str | None = None,
    model_id: str = VITMATTE_MODEL,
    predictor: Predictor | None = None,
) -> np.ndarray:
    """Alpha for ``composite``: the trimap's known regions verbatim, the model's
    estimate inside the unknown band. ``predictor`` overrides the backend (tests)."""
    if predictor is None:
        if backend == "vitmatte":
            predictor = _vitmatte(model_id, device)
        elif backend == "birefnet":
            predictor = _birefnet()
            tile = None  # it resizes internally; tiling only loses context
        else:
            raise ValueError(f"unknown matting backend {backend!r}; one of {BACKENDS}")
    if tile:
        pred = tiled(predictor, composite, trimap, tile)
    else:
        pred = predictor(composite, trimap)
    known = trimap.astype(np.float32) / 255.0
    alpha = np.where(trimap == 128, np.clip(pred, 0.0, 1.0), known)
    return alpha.astype(np.float32)
