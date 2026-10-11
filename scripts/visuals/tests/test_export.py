import export
import numpy as np
import pytest
from PIL import Image


def _masters(w=4096, h=3072):
    """A 4:3 plate whose rows encode their own index, plus two RGBA layers."""
    rows = (np.arange(h) * 255 // (h - 1)).astype(np.uint8)
    plate = np.repeat(np.repeat(rows[:, None, None], w, 1), 3, 2)
    plate[: h // 100] = (255, 0, 0)  # top band: red
    plate[-(h // 100) :] = (0, 0, 255)  # bottom band: blue
    layers = {}
    for level in (0, 1):
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[h // 3 : 2 * h // 3, w // 3 : 2 * w // 3] = (40, 200, 40, 255)
        layers[level] = rgba
    return plate, layers


@pytest.fixture(scope="module")
def masters_():
    return _masters()


def test_defaults_write_webp_at_the_stage_aspect(tmp_path, masters_):
    plate, layers = masters_
    written = export.export_assets(plate, layers, 1, tmp_path)
    assert sorted(p.name for p in written) == [
        "environment.webp",
        "intro.webp",
        "patient_0.webp",
        "patient_1.webp",
    ]
    for p in written:
        with Image.open(p) as img:
            assert img.format == "WEBP"
            assert img.size == (2048, 1507)  # 2048 / (1196/880)
    with Image.open(tmp_path / "patient_0.webp") as img:
        assert img.mode == "RGBA"
        lo, hi = img.getchannel("A").getextrema()
        assert lo == 0 and hi == 255
        assert img.getpixel((5, 5))[3] == 0 and img.getpixel((1024, 753))[3] == 255
    with Image.open(tmp_path / "environment.webp") as img:
        assert img.mode == "RGB"
    with Image.open(tmp_path / "intro.webp") as img:
        r, g, b = img.convert("RGB").getpixel((1024, 753))
        assert g > 150 and r < 100  # the start-level layer is composited in
    # Lossy WebP keeps the preloaded frames small.
    assert (tmp_path / "patient_0.webp").stat().st_size < 400_000


def test_four_by_three_keeps_the_whole_frame(tmp_path, masters_):
    plate, layers = masters_
    export.export_assets(plate, layers, 0, tmp_path, aspect=4 / 3, fmt="png")
    with Image.open(tmp_path / "environment.png") as img:
        assert img.size == (2048, 1536)
        top = img.convert("RGB").getpixel((1024, 2))
        bottom = img.convert("RGB").getpixel((1024, 1533))
    assert top[0] > 200 and top[2] < 50  # red band survives
    assert bottom[2] > 200 and bottom[0] < 50  # blue band survives


def test_crop_offset_is_honoured(tmp_path, masters_):
    plate, layers = masters_
    # 16:9 trims 768 rows; offset y=0 keeps the top band, y=1 the bottom one.
    export.export_assets(
        plate, layers, 0, tmp_path / "top", aspect=16 / 9, offset=(0.5, 0.0), width=512
    )
    export.export_assets(
        plate, layers, 0, tmp_path / "bot", aspect=16 / 9, offset=(0.5, 1.0), width=512
    )
    with Image.open(tmp_path / "top" / "environment.webp") as img:
        assert img.size == (512, 288)
        assert img.convert("RGB").getpixel((256, 1))[0] > 200
        r, _, b = img.convert("RGB").getpixel((256, 286))
        assert abs(r - b) < 20  # grey gradient: the blue band was cropped
    with Image.open(tmp_path / "bot" / "environment.webp") as img:
        r, _, b = img.convert("RGB").getpixel((256, 1))
        assert abs(r - b) < 20  # grey gradient: the red band was cropped
        assert img.convert("RGB").getpixel((256, 286))[2] > 200


def test_layers_must_match_the_plate_frame(tmp_path, masters_):
    plate, _ = masters_
    with pytest.raises(SystemExit):
        export.export_assets(plate, {0: np.zeros((10, 10, 4), np.uint8)}, 0, tmp_path)


def test_safe_area_check():
    shape = (3072, 4096)
    assert export.safe_area_kept(
        shape, 1196 / 880, (0.5, 0.5), (0.05, 0.02, 0.95, 0.98)
    )
    assert not export.safe_area_kept(
        shape, 1196 / 880, (0.5, 0.5), (0.0, 0.0, 1.0, 1.0)
    )
    assert not export.safe_area_kept(shape, 16 / 9, (0.5, 0.5), (0.1, 0.05, 0.9, 0.95))
