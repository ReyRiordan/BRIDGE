import matting
import numpy as np
import pytest
from synth import DRIFT, drifted, make_scene


@pytest.fixture(scope="module")
def scene():
    return make_scene()


@pytest.fixture(scope="module")
def aligned(scene):
    return matting.align_to_plate(scene.plate, drifted(scene.truth))


def _interior(shape, margin=12):
    m = np.zeros(shape[:2], bool)
    m[margin:-margin, margin:-margin] = True
    return m


def test_alignment_recovers_the_drift(scene):
    changed = (scene.alpha > 0.01) | (scene.shadow > 0.01)
    warp = matting.estimate_alignment(
        scene.plate, drifted(scene.truth), exclude=changed
    )
    np.testing.assert_allclose(warp[:, :2], DRIFT[:, :2], atol=2e-3)
    np.testing.assert_allclose(warp[:, 2], DRIFT[:, 2], atol=0.3)


def test_align_to_plate_without_a_mask_registers_the_background(scene, aligned):
    background = (
        (scene.alpha < 0.01) & (scene.shadow < 0.01) & _interior(scene.plate.shape)
    )
    before = np.abs(drifted(scene.truth).astype(int) - scene.plate.astype(int))[
        background
    ]
    after = np.abs(aligned.astype(int) - scene.plate.astype(int))[background]
    assert np.median(after) < 2
    assert np.median(after) < np.median(before) / 3


def test_diff_mask_finds_the_person_but_not_furniture_or_shadow(scene, aligned):
    mask = matting.diff_mask(scene.plate, aligned, threshold=0.1)
    truth = scene.alpha > 0.5
    iou = (mask & truth).sum() / (mask | truth).sum()
    assert iou > 0.9
    assert mask[scene.furniture].mean() < 0.01
    assert mask[scene.shadow > 0.2].mean() < 0.05


def test_trimap_band_covers_the_soft_edge(scene, aligned):
    mask = matting.diff_mask(scene.plate, aligned, threshold=0.1)
    trimap = matting.make_trimap(mask, erode_px=6, dilate_px=6)
    soft = (scene.alpha > 0.05) & (scene.alpha < 0.95)
    assert (trimap[soft] == 128).mean() > 0.95
    assert (trimap[scene.alpha > 0.99] == 0).sum() == 0
    assert (trimap[scene.alpha < 0.001] == 255).sum() == 0
    assert set(np.unique(trimap)) <= {0, 128, 255}


def test_decontaminate_recovers_foreground_colour(scene):
    # Pure colour algebra on the un-drifted truth: shadow-free pixels only.
    no_shadow = scene.shadow < 1e-3
    fg = matting.decontaminate(scene.truth, scene.plate, scene.alpha)
    edge = (scene.alpha > 0.3) & (scene.alpha < 0.97) & no_shadow
    err = np.abs(fg.astype(int) - scene.fg.astype(int))[edge]
    assert edge.sum() > 100
    assert err.mean() < 4
    # Naively keeping the composite colour leaves the plate in the edge.
    naive = np.abs(scene.truth.astype(int) - scene.fg.astype(int))[edge]
    assert naive.mean() > 3 * err.mean()


def test_shadow_gets_low_alpha_and_furniture_none(scene, aligned):
    person = matting.diff_mask(scene.plate, aligned, threshold=0.1)
    shadow = matting.shadow_alpha(scene.plate, aligned, person)
    core = (scene.shadow > 0.4) & (scene.alpha < 0.01)
    assert core.sum() > 100
    assert abs(shadow[core].mean() - scene.shadow[core].mean()) < 0.08
    assert shadow.max() <= 0.8
    far = (scene.shadow < 1e-3) & (scene.alpha < 1e-3) & _interior(scene.plate.shape)
    assert shadow[far].mean() < 0.01
    assert shadow[scene.furniture & (scene.shadow < 1e-3)].max() < 0.05


def test_match_exposure_undoes_a_global_gain(scene):
    gain = np.array([0.94, 0.95, 0.97], np.float32)
    darker = np.clip(scene.truth * gain, 0, 255).astype(np.uint8)
    changed = (scene.alpha > 0.01) | (scene.shadow > 0.01)
    fixed = matting.match_exposure(scene.plate, darker, exclude=changed)
    background = ~changed
    assert np.abs(fixed.astype(int) - scene.plate.astype(int))[background].mean() < 1
    # Without it the whole room near the person would turn into shadow.
    person = scene.alpha > 0.5
    haze = matting.shadow_alpha(scene.plate, darker, person)[background]
    clean = matting.shadow_alpha(scene.plate, fixed, person)[background]
    assert (haze > 0).mean() > 0.2
    assert (clean > 0).mean() < 0.01


def test_full_layer_recomposes_onto_the_plate(scene):
    # With the true alpha, person + shadow over the plate reproduces the frame.
    shadow = matting.shadow_alpha(scene.plate, scene.truth, scene.alpha > 0.5)
    fg = matting.decontaminate(scene.truth, scene.plate, scene.alpha)
    fg, alpha = matting.merge_shadow(fg, scene.alpha, shadow)
    rgba = matting.to_rgba(fg, alpha)
    assert rgba.shape == (*scene.plate.shape[:2], 4) and rgba.dtype == np.uint8
    out = matting.composite_over(rgba, scene.plate)
    err = np.abs(out.astype(int) - scene.truth.astype(int))
    assert np.median(err) <= 1
    assert err[scene.alpha > 0.5].mean() < 3


@pytest.mark.parametrize(
    ("shape", "aspect", "offset", "box"),
    [
        ((3072, 4096), 1196 / 880, (0.5, 0.5), (0, 29, 4096, 3043)),
        ((3072, 4096), 1196 / 880, (0.5, 0.0), (0, 0, 4096, 3014)),
        ((3072, 4096), 4 / 3, (0.5, 0.5), (0, 0, 4096, 3072)),
        ((3072, 4096), 16 / 9, (0.0, 1.0), (0, 768, 4096, 3072)),
        ((3000, 3000), 4 / 3, (1.0, 0.5), (0, 375, 3000, 2625)),
        ((900, 3000), 4 / 3, (1.0, 0.5), (1800, 0, 3000, 900)),
    ],
)
def test_crop_box(shape, aspect, offset, box):
    assert matting.crop_box(shape, aspect, offset) == box
    img = np.zeros((*shape, 3), np.uint8)
    out = matting.crop_to_aspect(img, aspect, offset)
    assert out.shape[:2] == (box[3] - box[1], box[2] - box[0])
