import re

import contact_sheet
import generate
import masters
import numpy as np
from common import save_png


def test_sheet_shows_accepted_runners_up_layers_and_rejections(tmp_path, monkeypatch):
    root = tmp_path / "work"
    monkeypatch.setenv("BRIDGE_VISUALS_WORK", str(root))
    monkeypatch.setattr(masters, "MANIFEST_DIR", tmp_path / "manifests")
    sid = "example"

    def add(key, rgb, **meta):
        save_png(np.full((300, 400, 3), rgb, np.uint8), root / key)
        masters.record(sid, key, {"size": [400, 300], "cost_usd": 0.113, **meta})

    add(f"{sid}/plate/plate_a_0.png", 120)
    add(f"{sid}/plate/plate_a_1.png", 130)
    masters.accept(sid, "plate", f"{sid}/plate/plate_a_0.png")
    for i in range(3):
        add(f"{sid}/composites/level_5/level_5_b_{i}.png", 60 + i)
    masters.accept(sid, "level_5", f"{sid}/composites/level_5/level_5_b_0.png")
    generate.reject(sid, "5", "level_5_b_2.png", "identity drift")
    layer = np.zeros((300, 400, 4), np.uint8)
    layer[100:200, 150:250] = (200, 40, 40, 255)
    save_png(layer, root / f"{sid}/layers/patient_5.png")
    add(f"{sid}/composites/level_4/level_4_c_0.png", 90)

    out = contact_sheet.build(sid)
    page = out.read_text()
    assert out == root / sid / "contact_sheet.html"
    assert page.count("<section") == 12  # plate + levels 0..10
    assert "Level 5 (anchor)" in page
    assert page.count('class="chip accepted"') == 2
    assert "1 awaiting review" in page  # level 4
    assert page.count("not generated") == 9
    assert "identity drift" in page and 'class="small rejected"' in page
    assert "layer over the plate" in page and "extracted alpha" in page
    assert 'class="box crop"' in page and 'class="box safe"' in page
    assert "$0.68" in page  # 6 candidates × $0.113
    # Every image the page references exists, relative to the page.
    for src in re.findall(r'src="([^"]+)"', page):
        assert (out.parent / src).exists(), src
    # Accepting a rejected candidate clears the flag.
    generate.accept(sid, "5", "level_5_b_2.png")
    assert not masters.load_manifest(sid)["objects"][
        f"{sid}/composites/level_5/level_5_b_2.png"
    ]["rejected"]
