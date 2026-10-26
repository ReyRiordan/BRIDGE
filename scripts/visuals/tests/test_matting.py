import cv2
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


def test_seamless_spreads_a_tone_step_and_keeps_detail(scene):
    plate = scene.plate
    h, w = plate.shape[:2]
    mask = np.zeros((h, w), bool)
    mask[100:300, 150:450] = True
    src = plate.astype(np.int16) + 25  # the edit: brighter, same texture...
    src[180:220, 280:320] -= 80  # ...plus a dark detail of its own
    src = np.clip(src, 0, 255).astype(np.uint8)
    out = matting.seamless(src, plate, mask)
    np.testing.assert_array_equal(out[~mask], plate[~mask])
    # No step at the edge: just inside, the +25 offset is already gone.
    edge = out[101, 150:450].astype(int) - plate[101, 150:450].astype(int)
    assert np.abs(edge).mean() < 3
    # The offset is gone, the detail stays.
    calm = mask.copy()
    calm[170:230, 270:330] = False
    assert np.abs(out[calm].astype(int) - plate[calm].astype(int)).mean() < 6
    detail = out[190:210, 290:310].astype(int) - plate[190:210, 290:310].astype(int)
    assert detail.mean() < -50


def _removed_shadow(scene):
    """A plate with a soft shadow beside the person that the edit removed."""
    h, w = scene.plate.shape[:2]
    blob = np.zeros((h, w), np.float32)
    # Within tone_layer's reach of the person, as a prop's own shadow is.
    cv2.circle(blob, (int(w * 0.62), int(h * 0.38)), 30, 0.35, -1)
    blob = cv2.GaussianBlur(blob, (0, 0), 8)
    plate = scene.plate.astype(np.float32) * (1 - blob[..., None])
    return np.clip(plate + 0.5, 0, 255).astype(np.uint8), blob


def test_tone_layer_carries_a_removed_shadow(scene):
    plate, blob = _removed_shadow(scene)
    person = scene.alpha > 0.5
    fg, a = matting.tone_layer(plate, scene.truth, person, 0.1, reach=0.4)
    out = matting.composite_over(matting.to_rgba(fg, a), plate)
    lit = (blob > 0.1) & ~matting.grow(person, 8)
    err = np.abs(out.astype(int) - scene.truth.astype(int)).max(-1)
    assert np.median(err[lit]) <= 4
    assert a[(blob > 0.25) & ~matting.grow(person, 8)].min() > 0.5  # carried
    far = ~matting.grow(person, 0.4 * max(plate.shape[:2]))
    assert a[far].max() == 0


def test_relight_extraction_leaves_no_seam(scene):
    import extract

    plate, blob = _removed_shadow(scene)
    composite = drifted(scene.truth)
    rgba, _ = extract.extract_layer(
        plate, composite, tile=None, relight=True, predictor=lambda i, t: scene.alpha
    )
    out = matting.composite_over(rgba, plate)
    err = np.abs(out.astype(int) - scene.truth.astype(int)).max(-1)
    lit = (blob > 0.02) & _interior(plate.shape) & (scene.alpha < 0.01)
    assert np.percentile(err[lit], 95) <= 8
    # Without relight, the brightening is cut off at the diff threshold.
    rgba0, _ = extract.extract_layer(
        plate, composite, tile=None, predictor=lambda i, t: scene.alpha
    )
    err0 = np.abs(matting.composite_over(rgba0, plate).astype(int) - scene.truth)
    assert np.percentile(err0.max(-1)[lit], 95) > 2 * np.percentile(err[lit], 95)


def test_diff_mask_catches_a_dark_hue_change_rgb_distance_misses():
    plate = np.full((300, 400, 3), (52, 48, 50), np.uint8)  # dark grey floor
    comp = plate.copy()
    comp[100:200, 150:250] = (68, 30, 38)  # dark burgundy cloth
    rgb = matting.diff_mask(plate, comp, 0.1, chroma_weight=0)
    hue = matting.diff_mask(plate, comp, 0.1)
    assert not rgb.any()
    assert hue[110:190, 160:240].all() and not hue[:90].any()
    # A shadow (same chroma, darker) still doesn't enter.
    shade = plate.copy()
    shade[100:200, 150:250] = (plate[100:200, 150:250] * 0.6).astype(np.uint8)
    assert not matting.diff_mask(plate, shade, 0.1, chroma_weight=2.0).any()


def test_close_mask_bridges_a_gap_without_growing():
    m = np.zeros((200, 200), bool)
    m[20:90, 80:120] = True  # gown
    m[110:180, 80:120] = True  # sock — a 20 px shin gap between them
    closed = matting.close_mask(m, 15)
    assert closed[90:110, 85:115].all()
    assert not closed[:15].any() and not closed[:, :70].any()
    assert closed.sum() < m.sum() + 40 * 25


def test_keep_near_drops_far_islands_only():
    m = np.zeros((200, 300), bool)
    m[40:160, 100:150] = True  # the subject
    m[165:180, 120:140] = True  # a sock just below it
    m[50:120, 260:290] = True  # a redrawn door leaf far away
    kept = matting.keep_near(m, 20)
    assert kept[100, 120] and kept[170, 130]
    assert not kept[80, 270]
