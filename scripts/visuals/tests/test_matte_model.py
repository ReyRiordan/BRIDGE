import subprocess
import sys
from pathlib import Path

import matte_model
import numpy as np
import pytest


def _trimap(h=300, w=400):
    t = np.zeros((h, w), np.uint8)
    t[100:200, 150:250] = 128
    t[120:180, 170:230] = 255
    return t


def _image(h=300, w=400):
    rng = np.random.default_rng(1)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def _pointwise(img, trimap):
    # Depends only on the pixel itself, so any tiling must reproduce it exactly.
    return img[..., 0].astype(np.float32) / 255.0


def test_tiled_matches_untiled_for_a_pointwise_model():
    img, tri = _image(), _trimap()
    full = _pointwise(img, tri)
    out = matte_model.tiled(_pointwise, img, tri, tile=64, overlap=16)
    unknown = tri == 128
    np.testing.assert_allclose(out[unknown], full[unknown], atol=1e-5)


def test_tiled_skips_tiles_without_unknown_pixels():
    img, tri = _image(), _trimap()
    calls = []

    def spy(i, t):
        calls.append(t.shape)
        assert (t == 128).any()
        return np.zeros(t.shape, np.float32)

    matte_model.tiled(spy, img, tri, tile=64, overlap=16)
    # The band spans 100x100 px of a 300x400 frame: 4x4 of the 6x8 tile grid
    # (stride 48) touch it, and the other 32 are never run.
    assert len(calls) == 16
    assert all(s[0] <= 64 and s[1] <= 64 for s in calls)


def test_estimate_alpha_keeps_known_regions_and_clips():
    img, tri = _image(), _trimap()
    alpha = matte_model.estimate_alpha(
        img, tri, tile=64, predictor=lambda i, t: np.full(t.shape, 1.7, np.float32)
    )
    assert alpha.dtype == np.float32
    assert (alpha[tri == 0] == 0).all()
    assert (alpha[tri == 255] == 1).all()
    assert (alpha[tri == 128] == 1).all()


def test_unknown_backend_raises():
    with pytest.raises(ValueError):
        matte_model.estimate_alpha(_image(), _trimap(), backend="nope")


def test_importing_the_tooling_does_not_import_torch_or_genai():
    here = Path(__file__).resolve().parent.parent
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import matting, matte_model, masters, generate, extract, export, contact_sheet;"
        "bad = [m for m in ('torch', 'transformers', 'google.genai') if m in sys.modules];"
        "assert not bad, bad"
    ) % str(here)
    subprocess.run([sys.executable, "-c", code], check=True)
