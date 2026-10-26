import re

import contact_sheet
import generate
import masters
import numpy as np
from common import save_png
from PIL import Image


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
    # An extracted start-state layer, and one not yet extracted.
    add(f"{sid}/composites/security_present/security_present_d_0.png", 70)
    masters.accept(
        sid,
        "security_present",
        f"{sid}/composites/security_present/security_present_d_0.png",
    )
    staff = np.zeros((300, 400, 4), np.uint8)
    staff[0:50, 0:50] = (40, 40, 200, 255)
    save_png(staff, root / f"{sid}/layers/security_present.png")

    out = contact_sheet.build(sid)
    page = out.read_text()
    assert out == root / sid / "contact_sheet.html"
    assert page.count("<section") == 14  # plate + 2 layers + levels 0..10
    assert "Level 5 (anchor)" in page
    assert "Layer security_present (start state)" in page
    assert "Layer blinds_closed<" in page
    assert page.index('id="blinds_closed"') < page.index('id="level_0"')
    assert page.count('class="chip accepted"') == 3
    assert "1 awaiting review" in page  # level 4
    assert page.count("not generated") == 10  # 9 levels + blinds_closed
    assert "identity drift" in page and 'class="small rejected"' in page
    assert "layer over the plate" in page and "start state<" in page
    assert page.count("extracted alpha") == 2
    assert 'class="box crop"' in page and 'class="box safe"' in page
    assert "$0.79" in page  # 7 candidates × $0.113
    # A level previews over the start state: plate + security_present + patient.
    over = np.asarray(Image.open(out.parent / "previews/layers/over_patient_5.jpg"))
    assert over[5, 5, 2] > 150 and over[5, 5, 0] < 100  # the staff layer
    assert over[150, 200, 0] > 150  # the patient
    over = np.asarray(
        Image.open(out.parent / "previews/layers/over_security_present.jpg")
    )
    assert over[150, 200, 0] < 150  # a layer previews over the bare plate
    # Every image the page references exists, relative to the page.
    for src in re.findall(r'src="([^"]+)"', page):
        assert (out.parent / src).exists(), src
    # Accepting a rejected candidate clears the flag.
    generate.accept(sid, "5", "level_5_b_2.png")
    assert not masters.load_manifest(sid)["objects"][
        f"{sid}/composites/level_5/level_5_b_2.png"
    ]["rejected"]
