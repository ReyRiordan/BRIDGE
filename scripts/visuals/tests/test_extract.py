import extract
import matting
import numpy as np
from synth import drifted, make_scene


def test_extract_layer_end_to_end_with_an_oracle_matte():
    """The whole pipeline, with the matting model replaced by the true alpha
    resampled the same way (the only step a model owns)."""
    scene = make_scene()
    composite = drifted(scene.truth)

    def oracle(img, trimap):
        return scene.alpha

    rgba, stats = extract.extract_layer(
        scene.plate, composite, tile=None, predictor=oracle
    )
    assert rgba.shape == (*scene.plate.shape[:2], 4)
    assert stats["person_px"] > 0 and stats["shadow_px"] > 0

    out = matting.composite_over(rgba, scene.plate)
    err = np.abs(out.astype(int) - scene.truth.astype(int))
    interior = np.zeros(err.shape[:2], bool)
    interior[12:-12, 12:-12] = True
    assert np.median(err[interior]) <= 2
    # The furniture the patient sits on stays in the plate, not the layer.
    assert rgba[..., 3][scene.furniture & (scene.shadow < 1e-3)].max() < 13
    # The shadow comes through as low, black alpha.
    core = (scene.shadow > 0.4) & (scene.alpha < 0.01)
    assert 0.3 < rgba[..., 3][core].mean() / 255 < 0.65
    assert rgba[..., :3][core].max() < 40
