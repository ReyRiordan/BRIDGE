"""Clean-plate layer extraction — pure functions, no network, no model.

Images are ``HxWx3`` uint8 RGB arrays; masks are ``HxW`` bool; alpha is
``HxW`` float32 in [0, 1]; a trimap is ``HxW`` uint8 with 0 = background,
128 = unknown, 255 = foreground.

The pipeline (driven by ``extract.py``)::

    align_to_plate → match_exposure → diff_mask → make_trimap → [matte_model.estimate_alpha]
        → decontaminate → shadow_alpha → merge_shadow → to_rgba

With ``relight`` (props and their lighting), ``tone_layer`` replaces the
shadow step: it carries any smooth brightening *or* darkening around the
subject as a soft, exact layer, so a removed shadow leaves no seam.
"""

from __future__ import annotations

import cv2
import numpy as np

# Below this alpha a pixel's colour is unrecoverable (division by ~0).
_MIN_ALPHA = 0.02


def _scale_px(shape: tuple[int, ...], frac: float, floor: int = 1) -> int:
    return max(floor, int(round(max(shape[:2]) * frac)))


def _disk(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
    )


def grow(mask: np.ndarray, radius: float) -> np.ndarray:
    """Dilate a bool mask by a Euclidean ``radius`` — a distance transform, so
    the cost doesn't scale with the radius the way a disk kernel's does."""
    if radius <= 0:
        return mask.astype(bool)
    dist = cv2.distanceTransform((~mask.astype(bool)).astype(np.uint8), cv2.DIST_L2, 5)
    return dist <= radius


def shrink(mask: np.ndarray, radius: float) -> np.ndarray:
    """Erode a bool mask by a Euclidean ``radius``."""
    if radius <= 0:
        return mask.astype(bool)
    return cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5) > radius


def crop_box(
    shape: tuple[int, ...], aspect: float, offset: tuple[float, float] = (0.5, 0.5)
) -> tuple[int, int, int, int]:
    """The ``(x0, y0, x1, y1)`` pixel box ``crop_to_aspect`` keeps.

    ``offset`` places the crop inside the slack: (0, 0) = top-left,
    (0.5, 0.5) = centred, (1, 1) = bottom-right.
    """
    h, w = shape[:2]
    ox, oy = (min(max(v, 0.0), 1.0) for v in offset)
    if w / h > aspect:
        cw, ch = int(round(h * aspect)), h
    else:
        cw, ch = w, int(round(w / aspect))
    x0 = int(round((w - cw) * ox))
    y0 = int(round((h - ch) * oy))
    return x0, y0, x0 + cw, y0 + ch


def crop_to_aspect(
    img: np.ndarray, aspect: float, offset: tuple[float, float] = (0.5, 0.5)
) -> np.ndarray:
    x0, y0, x1, y1 = crop_box(img.shape, aspect, offset)
    return img[y0:y1, x0:x1]


def _grey(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0


def estimate_alignment(
    plate: np.ndarray, composite: np.ndarray, exclude: np.ndarray | None = None
) -> np.ndarray:
    """The 2x3 affine mapping plate coordinates → composite coordinates.

    ECC runs coarse (≤1024 px) then refines at full resolution, on the
    pixels outside ``exclude`` (the person — the only thing allowed to differ).
    """
    if plate.shape != composite.shape:
        composite = cv2.resize(
            composite, (plate.shape[1], plate.shape[0]), cv2.INTER_AREA
        )
    tmpl, inp = _grey(plate), _grey(composite)
    valid = np.full(tmpl.shape, 255, np.uint8) if exclude is None else (~exclude * 255)
    valid = valid.astype(np.uint8)

    warp = np.eye(2, 3, dtype=np.float32)
    scale = min(1.0, 1024 / max(tmpl.shape))
    if scale < 1.0:
        size = (int(tmpl.shape[1] * scale), int(tmpl.shape[0] * scale))
        small = [cv2.resize(a, size, interpolation=cv2.INTER_AREA) for a in (tmpl, inp)]
        small_valid = cv2.resize(valid, size, interpolation=cv2.INTER_NEAREST)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
        _, warp = cv2.findTransformECC(
            small[0], small[1], warp, cv2.MOTION_AFFINE, criteria, small_valid, 5
        )
        warp[:, 2] /= scale
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        50 if scale < 1 else 200,
        1e-7,
    )
    try:
        _, warp = cv2.findTransformECC(
            tmpl, inp, warp, cv2.MOTION_AFFINE, criteria, valid, 5
        )
    except cv2.error:
        # Full-res refinement failed to converge — the coarse estimate stands.
        if scale == 1.0:
            raise
    return warp


def warp_to_plate(
    plate: np.ndarray, composite: np.ndarray, warp: np.ndarray
) -> np.ndarray:
    """Resample ``composite`` into plate coordinates; uncovered border = plate."""
    h, w = plate.shape[:2]
    if composite.shape[:2] != (h, w):
        composite = cv2.resize(composite, (w, h), interpolation=cv2.INTER_AREA)
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    aligned = cv2.warpAffine(composite, warp, (w, h), flags=flags)
    cover = cv2.warpAffine(np.full((h, w), 255, np.uint8), warp, (w, h), flags=flags)
    cover = cv2.erode(cover, _disk(1)) == 255
    aligned[~cover] = plate[~cover]
    return aligned


def align_to_plate(
    plate: np.ndarray, composite: np.ndarray, exclude: np.ndarray | None = None
) -> np.ndarray:
    """Re-register a model edit onto the plate it was made from.

    Without ``exclude`` the person (and their shade — anything that changed)
    is found in two passes: a generous diff of the unaligned pair, then a
    tighter diff once roughly aligned. The result is in plate coordinates.
    """
    if exclude is not None:
        return warp_to_plate(
            plate, composite, estimate_alignment(plate, composite, exclude)
        )
    radius = _scale_px(plate.shape, 0.02)
    resized = composite
    if composite.shape != plate.shape:
        resized = cv2.resize(
            composite, (plate.shape[1], plate.shape[0]), cv2.INTER_AREA
        )
    rough = diff_mask(plate, resized, 0.2, ignore_shading=False)
    rough = grow(rough, radius)
    aligned = warp_to_plate(
        plate, composite, estimate_alignment(plate, composite, rough)
    )
    tight = diff_mask(plate, aligned, 0.05, ignore_shading=False)
    tight = grow(tight, radius)
    return warp_to_plate(plate, composite, estimate_alignment(plate, composite, tight))


def shading(
    plate: np.ndarray,
    composite: np.ndarray,
    max_alpha: float = 0.8,
    chroma_tol: float = 0.08,
    min_ncc: float = 0.5,
    lighter: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """``(luminance ratio C/B, is_shading)`` per pixel.

    ``lighter`` also counts the mirror case — the plate, uniformly brighter
    with its texture intact (a shadow the edit removed) — as shading.

    A shadow is the plate, darker: every channel drops by about the same
    ratio *and* the plate's local texture survives (normalised cross-
    correlation of the two greys). A person replaces the texture, so dark
    clothing on a textured wall is not mistaken for shade. On a flat patch
    (no texture to compare) a flat result counts as shade.
    """
    sigma = max(1.0, max(plate.shape[:2]) / 1000)
    b = cv2.GaussianBlur(plate, (0, 0), sigma).astype(np.float32) + 1.0
    c = cv2.GaussianBlur(composite, (0, 0), sigma).astype(np.float32) + 1.0
    ratio = c / b
    lum = ratio.mean(axis=2)
    uniform = (ratio.max(axis=2) - ratio.min(axis=2)) < chroma_tol
    darker = (lum < 1.0) & (lum >= 1.0 - max_alpha)
    if lighter:
        darker |= (lum > 1.0) & (lum <= 1.0 / (1.0 - max_alpha))

    # Texture test on high-passed log intensity: a shadow multiplies the plate
    # by a smooth factor, which the log makes additive and the high-pass drops.
    win = (_scale_px(plate.shape, 1 / 150, 3),) * 2
    big = _scale_px(plate.shape, 1 / 100, 2)
    gb, gc = (np.log(_grey(img) + 0.02) for img in (plate, composite))
    gb = gb - cv2.GaussianBlur(gb, (0, 0), big)
    gc = gc - cv2.GaussianBlur(gc, (0, 0), big)
    mb, mc = cv2.blur(gb, win), cv2.blur(gc, win)
    vb = np.maximum(cv2.blur(gb * gb, win) - mb * mb, 0)
    vc = np.maximum(cv2.blur(gc * gc, win) - mc * mc, 0)
    cov = cv2.blur(gb * gc, win) - mb * mc
    ncc = cov / (np.sqrt(vb * vc) + 1e-8)
    flat = 1e-4
    textured_match = (vb > flat) & (ncc > min_ncc)
    flat_match = (vb <= flat) & (vc <= flat * 4)
    return lum, uniform & darker & (textured_match | flat_match)


def match_exposure(
    plate: np.ndarray, composite: np.ndarray, exclude: np.ndarray | None = None
) -> np.ndarray:
    """Undo a global per-channel gain the edit applied to the whole frame.

    Generative edits often shift the room's exposure by a few percent, which
    would otherwise read as a faint shadow (or a diff) everywhere. The gain is
    the median ``B/C`` over pixels outside ``exclude``.
    """
    keep = np.ones(plate.shape[:2], bool) if exclude is None else ~exclude
    b = plate[keep].astype(np.float32) + 1.0
    c = composite[keep].astype(np.float32) + 1.0
    gain = np.median(b / c, axis=0)
    out = composite.astype(np.float32) * gain
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def diff_mask(
    plate: np.ndarray,
    composite: np.ndarray,
    threshold: float,
    min_area: float = 5e-4,
    max_hole: float = 2e-3,
    ignore_shading: bool = True,
    ignore_lighting: bool = False,
    chroma_weight: float = 2.0,
) -> np.ndarray:
    """Where ``composite`` differs from ``plate`` — the person.

    ``threshold`` is the max per-channel difference in [0, 1] after a light
    blur, or ``chroma_weight`` × the Lab chroma (a*, b*) difference if that
    is larger: dark clothing over a dark patch of floor differs mostly in hue,
    which RGB distance under-counts. Shade keeps its chroma, so it doesn't
    enter through this term. Shade the person casts (see ``shading``) is left out — it becomes
    the shadow layer instead. Specks below ``min_area`` (fraction of the
    frame) are dropped and holes below ``max_hole`` filled (clothing that
    happens to match the wall). Plate-identical furniture never enters it.
    ``ignore_lighting`` also leaves out uniform brightening (``shading``'s
    ``lighter``), for ``tone_layer`` to carry.
    """
    sigma = max(1.0, max(plate.shape[:2]) / 2000)
    a = cv2.GaussianBlur(plate, (0, 0), sigma).astype(np.int16)
    b = cv2.GaussianBlur(composite, (0, 0), sigma).astype(np.int16)
    d = np.abs(a - b).max(axis=2).astype(np.float32) / 255.0
    if chroma_weight:
        la, lb = (
            cv2.cvtColor(x.astype(np.uint8), cv2.COLOR_RGB2LAB)[..., 1:].astype(
                np.int16
            )
            for x in (a, b)
        )
        chroma = np.abs(la - lb).max(axis=2).astype(np.float32) / 255.0
        d = np.maximum(d, chroma * chroma_weight)
    m = d > threshold
    if ignore_shading or ignore_lighting:
        m &= ~shading(plate, composite, lighter=ignore_lighting)[1]
    m = m.astype(np.uint8)

    k = _disk(_scale_px(plate.shape, 1 / 800))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)

    total = m.shape[0] * m.shape[1]
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area * total
    m = keep[labels]

    holes = (~m).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(holes, connectivity=4)
    h, w = m.shape
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        touches_border = x == 0 or y == 0 or x + bw == w or y + bh == h
        if not touches_border and area < max_hole * total:
            m[labels == i] = True
    return m


def close_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    """Morphological closing: bridges gaps narrower than ``2·radius`` (a shin
    lost against a shadow of the same tone) without growing the outline."""
    m = mask.astype(np.uint8)
    pad = radius + 1
    m = cv2.copyMakeBorder(m, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, _disk(radius))
    return m[pad:-pad, pad:-pad].astype(bool)


def keep_near(mask: np.ndarray, radius: int) -> np.ndarray:
    """Only the largest island and the islands within ``radius`` of it: drops
    stray far-off changes (a redrawn door leaf, floor reflections) while a
    separate sock or hand next to the subject stays."""
    m = mask.astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 2:
        return mask.astype(bool)
    main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    zone = grow(labels == main, radius)
    keep = np.unique(labels[zone & (labels > 0)])
    return np.isin(labels, keep)


def make_trimap(mask: np.ndarray, erode_px: int, dilate_px: int) -> np.ndarray:
    """Sure-foreground core, an unknown band of ``erode_px + dilate_px``, background."""
    fg = shrink(mask, erode_px)
    maybe = grow(mask, dilate_px)
    trimap = np.zeros(mask.shape, np.uint8)
    trimap[maybe] = 128
    trimap[fg] = 255
    return trimap


def decontaminate(
    composite: np.ndarray, plate: np.ndarray, alpha: np.ndarray
) -> np.ndarray:
    """Remove the plate's colour from soft edges: ``F = (C − (1−α)·B) / α``.

    Exact because the background ``B`` is known. Pixels with α below
    ``_MIN_ALPHA`` keep the composite colour (they are invisible anyway).
    """
    c = composite.astype(np.float32)
    b = plate.astype(np.float32)
    a = alpha.astype(np.float32)[..., None]
    safe = np.maximum(a, _MIN_ALPHA)
    f = (c - (1.0 - a) * b) / safe
    f = np.where(a >= _MIN_ALPHA, f, c)
    return np.clip(f + 0.5, 0, 255).astype(np.uint8)


def shadow_alpha(
    plate: np.ndarray,
    composite: np.ndarray,
    person_mask: np.ndarray,
    reach: float = 0.25,
    max_alpha: float = 0.8,
    min_alpha: float = 0.04,
) -> np.ndarray:
    """Alpha of a black shadow layer that turns ``plate`` into ``composite``.

    For a black layer over ``B``, ``C = (1−α)·B``, so ``α = 1 − C/B``. Only
    pixels outside the person, within ``reach`` (fraction of the frame) of
    them, that ``shading`` calls shade count.
    """
    sigma = max(1.0, max(plate.shape[:2]) / 1000)
    lum, shade = shading(plate, composite, max_alpha=max_alpha)

    dist = cv2.distanceTransform(
        (~person_mask.astype(bool)).astype(np.uint8), cv2.DIST_L2, 5
    )
    outside = dist > _scale_px(plate.shape, 1 / 500)
    near = dist <= _scale_px(plate.shape, reach)

    # Close the shade mask first: the texture test misses scattered pixels,
    # and blurring zeros into a shadow would thin it.
    k = _disk(_scale_px(plate.shape, 1 / 300))
    region = cv2.morphologyEx(shade.astype(np.uint8), cv2.MORPH_CLOSE, k)
    region = cv2.morphologyEx(region, cv2.MORPH_OPEN, k) > 0
    a = np.clip(1.0 - lum, 0.0, max_alpha)
    a[~(region & outside & near)] = 0.0
    a = cv2.GaussianBlur(a, (0, 0), sigma)
    a[a < min_alpha] = 0.0
    a[~outside] = 0.0
    return a.astype(np.float32)


def tone_layer(
    plate: np.ndarray,
    composite: np.ndarray,
    subject_mask: np.ndarray,
    threshold: float = 0.1,
    reach: float = 0.12,
    noise: float = 0.03,
) -> tuple[np.ndarray, np.ndarray]:
    """``(colour, alpha)`` of a soft layer that carries the edit's smooth
    lighting change around the subject onto the plate.

    ``D`` is the low-passed difference ``C − B`` (re-render texture noise
    stays out). Alpha ramps with ``|D|`` up to ``threshold`` and the colour
    is ``F = B + D/α``, so over the plate ``α·F + (1−α)·B = B + D`` exactly,
    whatever the sign of ``D`` — a seamless fade instead of a cut-off at the
    diff threshold. Only pixels outside the subject, within ``reach``
    (fraction of the frame) of it, with ``|D|`` above ``noise``, count.
    """
    sigma = max(1.0, max(plate.shape[:2]) / 300)
    b = plate.astype(np.float32)
    dist = cv2.distanceTransform(
        (~subject_mask.astype(bool)).astype(np.uint8), cv2.DIST_L2, 5
    )
    # Normalised convolution over pixels clear of the subject: the subject's
    # own large difference must not bleed into the lighting estimate beside it.
    w = (dist > _scale_px(plate.shape, 1 / 200)).astype(np.float32)
    raw = (composite.astype(np.float32) - b) * w[..., None]
    norm = cv2.GaussianBlur(w, (0, 0), sigma)[..., None]
    d = cv2.GaussianBlur(raw, (0, 0), sigma) / np.maximum(norm, 1e-3)
    d[norm[..., 0] < 0.05] = 0.0
    mag = np.abs(d).max(axis=2) / 255.0
    near = (dist > 0) & (dist <= _scale_px(plate.shape, reach))
    a = np.clip((mag - noise) / max(threshold - noise, 1e-3), 0.0, 1.0)
    a[~near] = 0.0
    # Fade out toward the reach limit, so the layer has no outer edge either.
    a *= np.clip(1.0 - dist / _scale_px(plate.shape, reach), 0.0, 1.0) ** 0.5
    a = cv2.GaussianBlur(a, (0, 0), sigma).astype(np.float32)
    a[a < 0.01] = 0.0
    a[~near] = 0.0
    f = b + d / np.maximum(a, 0.01)[..., None]
    f = np.clip(f + 0.5, 0, 255).astype(np.uint8)
    return f, a


def _push_pull(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Fill ``values`` (known where ``weights`` > 0) smoothly everywhere: a
    pyramid push-pull, the cheap stand-in for a membrane (Laplace) solve."""
    vs, ws = [values * weights[..., None]], [weights]
    while min(ws[-1].shape) > 8:
        vs.append(cv2.pyrDown(vs[-1]))
        ws.append(cv2.pyrDown(ws[-1]))
    est = vs[-1] / np.maximum(ws[-1], 1e-6)[..., None]
    for v, w in zip(reversed(vs[:-1]), reversed(ws[:-1])):
        up = cv2.pyrUp(est, dstsize=(w.shape[1], w.shape[0]))
        a = np.clip(w * 4.0, 0.0, 1.0)[..., None]
        est = a * (v / np.maximum(w, 1e-6)[..., None]) + (1.0 - a) * up
    return est


def seamless(src: np.ndarray, dst: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """``src`` inside ``mask``, blended into ``dst`` so it meets ``dst``
    exactly on the mask's edge: the mismatch on a one-pixel ring around the
    mask is spread smoothly across the interior. A smooth tone offset
    between the two fades out instead of showing as a step; ``src``'s own
    detail is kept. Outside the mask the result is ``dst``."""
    m = mask.astype(bool)
    if not m.any():
        return dst.copy()
    ring = grow(m, 1.5) & ~m
    diff = dst.astype(np.float32) - src.astype(np.float32)
    corr = _push_pull(diff * ring[..., None], ring.astype(np.float32))
    # The ring's own values are exact; push-pull only fills the interior.
    out = np.where(m[..., None], src.astype(np.float32) + corr, dst.astype(np.float32))
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def merge_layers(
    fg: np.ndarray, alpha: np.ndarray, under: np.ndarray, under_alpha: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """``fg`` over ``under``, as one straight-alpha layer."""
    ap = alpha.astype(np.float32)
    au = under_alpha.astype(np.float32) * (1.0 - ap)
    out_a = ap + au
    mix = (
        fg.astype(np.float32) * ap[..., None] + under.astype(np.float32) * au[..., None]
    )
    out_fg = mix / np.maximum(out_a, 1e-6)[..., None]
    out_fg = np.where(out_a[..., None] > 0, out_fg, 0.0)
    return np.clip(out_fg + 0.5, 0, 255).astype(np.uint8), out_a


def merge_shadow(
    fg: np.ndarray, alpha: np.ndarray, shadow: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Person over a black shadow, as one straight-alpha layer."""
    ap = alpha.astype(np.float32)
    a_s = shadow.astype(np.float32) * (1.0 - ap)
    out_a = ap + a_s
    weight = np.where(out_a > 0, ap / np.maximum(out_a, 1e-6), 0.0)[..., None]
    out_fg = np.clip(fg.astype(np.float32) * weight + 0.5, 0, 255).astype(np.uint8)
    return out_fg, out_a


def to_rgba(fg: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    a = np.clip(alpha.astype(np.float32) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return np.dstack([fg, a])


def composite_over(rgba: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Straight-alpha ``rgba`` over an RGB ``background`` (what the stage shows)."""
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    out = rgba[..., :3].astype(np.float32) * a + background.astype(np.float32) * (
        1.0 - a
    )
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)
