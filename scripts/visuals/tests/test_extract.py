import extract
import masters
import matting
import numpy as np
import pytest
from common import save_png
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


def test_cli_writes_action_layers_and_levels(tmp_path, monkeypatch):
    root = tmp_path / "work"
    monkeypatch.setenv("BRIDGE_VISUALS_WORK", str(root))
    monkeypatch.setattr(masters, "MANIFEST_DIR", tmp_path / "manifests")
    sid = "example"
    sources = {}
    for slot in ("plate", "blinds_closed", "level_5"):
        key = f"{sid}/composites/{slot}/c.png"
        save_png(np.full((30, 40, 3), len(sources) * 50, np.uint8), root / key)
        masters.record(sid, key, {})
        masters.accept(sid, slot, key)
        sources[slot] = key
    seen = []

    def fake_extract(plate, composite, **kw):
        seen.append(int(composite[0, 0, 0]))
        return np.zeros((30, 40, 4), np.uint8), {"erode_px": 1, "dilate_px": 2}

    monkeypatch.setattr(extract, "extract_layer", fake_extract)
    assert extract.main([sid, "blinds_closed"]) == 0
    assert (root / f"{sid}/layers/blinds_closed.png").exists()
    entry = masters.load_manifest(sid)["objects"][f"{sid}/layers/blinds_closed.png"]
    assert entry["source"] == sources["blinds_closed"]
    assert entry["plate"] == sources["plate"]

    seen.clear()
    assert extract.main([sid, "all"]) == 0
    assert seen == [50, 100]  # the accepted layer, then the accepted level
    assert (root / f"{sid}/layers/patient_5.png").exists()
    with pytest.raises(SystemExit, match="not a level or a layer"):
        extract.main([sid, "door_closed"])
    with pytest.raises(SystemExit, match="security_present has no accepted"):
        extract.main([sid, "security_present"])
