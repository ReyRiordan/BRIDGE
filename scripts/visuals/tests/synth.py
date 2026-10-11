"""A synthetic 4:3 scene with known ground truth, shared by the tests."""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Scene:
    plate: np.ndarray  # HxWx3 uint8
    fg: np.ndarray  # HxWx3 uint8, the person's true colour
    alpha: np.ndarray  # HxW float32, the person's true alpha
    shadow: np.ndarray  # HxW float32, true shadow alpha (black layer)
    truth: np.ndarray  # plate + shadow + person, in plate coordinates
    furniture: np.ndarray  # HxW bool, the chair (identical in plate and composite)


def _ellipse(shape, centre, axes, sigma):
    m = np.zeros(shape, np.float32)
    cv2.ellipse(m, centre, axes, 0, 0, 360, 1.0, -1)
    return cv2.GaussianBlur(m, (0, 0), sigma) if sigma else m


def make_scene(w: int = 640, h: int = 480, seed: int = 0) -> Scene:
    rng = np.random.default_rng(seed)
    # Textured room: smooth noise over a vertical gradient (ECC needs texture).
    noise = cv2.GaussianBlur(rng.normal(0, 1, (h, w, 3)).astype(np.float32), (0, 0), 3)
    noise = noise / noise.std() * 18
    grad = np.linspace(170, 120, h, dtype=np.float32)[:, None, None]
    base = np.array([1.0, 0.95, 0.85], np.float32)
    plate = np.clip(grad * base + noise, 0, 255)
    furniture = np.zeros((h, w), bool)
    furniture[int(h * 0.55) : int(h * 0.8), int(w * 0.3) : int(w * 0.7)] = True
    plate[furniture] = plate[furniture] * 0.4 + np.array([60, 35, 20], np.float32) * 0.6
    plate = plate.astype(np.uint8)

    alpha = _ellipse((h, w), (w // 2, int(h * 0.45)), (w // 10, int(h * 0.28)), 1.5)
    fg_noise = (
        cv2.GaussianBlur(rng.normal(0, 1, (h, w, 3)).astype(np.float32), (0, 0), 2) * 10
    )
    fg = np.clip(np.array([40, 70, 160], np.float32) + fg_noise, 0, 255).astype(
        np.uint8
    )

    shadow = 0.5 * _ellipse((h, w), (int(w * 0.6), int(h * 0.8)), (w // 6, h // 20), 3)
    shaded = plate.astype(np.float32) * (1.0 - shadow[..., None])
    a = alpha[..., None]
    truth = fg.astype(np.float32) * a + shaded * (1.0 - a)
    truth = np.clip(truth + 0.5, 0, 255).astype(np.uint8)
    return Scene(plate, fg, alpha, shadow, truth, furniture & (alpha < 0.01))


# Plate → composite drift a generative edit typically introduces.
DRIFT = np.array([[1.004, 0.002, 3.5], [-0.0015, 0.997, -2.25]], np.float32)


def drifted(img: np.ndarray, warp: np.ndarray = DRIFT) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.warpAffine(
        img, warp, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT
    )
