import pytest
import yaml
from common import SHOTS_DIR, action_layer_key, candidate_key, load_shot_list


def test_example_parses_layers_size_and_start_state():
    shot = load_shot_list("example")
    assert shot.size == "4K"
    assert list(shot.layers) == ["security_present", "blinds_closed"]
    assert shot.layers["blinds_closed"].startswith("Close the window blinds")
    assert shot.start_visible == ("security_present",)
    assert not shot.extra


def test_layers_are_optional(tmp_path):
    raw = yaml.safe_load((SHOTS_DIR / "example.yaml").read_text())
    for k in ("layers", "start_visible", "size"):
        raw.pop(k)
    (tmp_path / "bare.yaml").write_text(yaml.safe_dump(raw))
    shot = load_shot_list("bare", tmp_path)
    assert shot.layers == {} and shot.start_visible == () and shot.size is None


@pytest.mark.parametrize(
    "layers, start_visible, match",
    [
        ({"Bad-Name": {"prompt": "x"}}, [], "snake_case"),
        ({"intro": {"prompt": "x"}}, [], "reserved"),
        ({"patient_3": {"prompt": "x"}}, [], "reserved"),
        ({"level_2": {"prompt": "x"}}, [], "reserved"),
        ({"door": {}}, [], "needs a prompt"),
        ({"door": "close it"}, [], "needs a prompt"),
        ({"door": {"prompt": "x"}}, ["curtain"], "unknown layer"),
    ],
)
def test_layer_validation(tmp_path, layers, start_visible, match):
    raw = yaml.safe_load((SHOTS_DIR / "example.yaml").read_text())
    raw["layers"], raw["start_visible"] = layers, start_visible
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(SystemExit, match=match):
        load_shot_list("bad", tmp_path)


def test_keys():
    assert candidate_key("s", "plate", "a.png") == "s/plate/a.png"
    assert candidate_key("s", "level_3", "a.png") == "s/composites/level_3/a.png"
    assert (
        candidate_key("s", "door_closed", "a.png") == "s/composites/door_closed/a.png"
    )
    assert action_layer_key("s", "door_closed") == "s/layers/door_closed.png"
