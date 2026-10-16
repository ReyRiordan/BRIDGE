import io
from types import SimpleNamespace

import generate
import masters
import pytest
from common import load_shot_list
from PIL import Image


@pytest.fixture
def shot():
    return load_shot_list("example")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated work dir + manifest dir, and no API key anywhere."""
    monkeypatch.setenv("BRIDGE_VISUALS_WORK", str(tmp_path / "work"))
    monkeypatch.setattr(masters, "MANIFEST_DIR", tmp_path / "manifests")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path


def _empty(sid="example"):
    return {"scenario_id": sid, "accepted": {}, "objects": {}}


def test_chain_dry_run_plans_every_level_without_a_key(env, capsys, shot):
    assert generate.main(["chain", "example", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "gemini-nano-banana-2.1 · 4K · 4:3 · standard" in out
    assert "11 request(s), 33 image(s)" in out


def test_chain_order_is_anchor_then_down_then_up(shot):
    plan = generate.plan_chain(shot, 3, _empty())
    assert [r.level for r in plan] == [5, 4, 3, 2, 1, 0, 6, 7, 8, 9, 10]


def test_anchor_is_edited_into_the_plate_and_attached_to_every_other_frame(shot):
    plan = generate.plan_chain(shot, 3, _empty())
    anchor, rest = plan[0], plan[1:]
    assert anchor.is_anchor
    assert [(r.role, r.key) for r in anchor.refs] == [("plate", "<accepted plate>")]
    for r in rest:
        roles = {ref.role: ref.key for ref in r.refs}
        assert roles["anchor"] == "<accepted level_5>"
        neighbour = r.level + 1 if r.level < 5 else r.level - 1
        assert roles["source"] == f"<accepted level_{neighbour}>"
        assert shot.frames[r.level] in r.prompt


def test_plan_uses_accepted_keys_when_they_exist(shot):
    m = _empty()
    m["accepted"] = {
        "plate": "example/plate/p.png",
        "level_5": "example/composites/level_5/a.png",
    }
    req = generate.plan_frame(shot, 6, 2, m)
    assert {r.role: r.key for r in req.refs} == {
        "source": "example/composites/level_5/a.png",
        "anchor": "example/composites/level_5/a.png",
    }
    assert generate.plan_frame(shot, 5, 2, m).refs[0].key == "example/plate/p.png"
    assert (
        generate.plan_frame(shot, 4, 1, m, from_key="example/x.png").refs[0].key
        == "example/x.png"
    )


def test_cost_estimate(shot):
    plan = generate.plan_chain(shot, 3, _empty())
    images = 33
    std = generate.estimate_cost(plan, "gemini-nano-banana-2.1", "4K", batch=False)
    bat = generate.estimate_cost(plan, "gemini-nano-banana-2.1", "4K", batch=True)
    assert images * 0.113 < std < images * 0.113 + 0.25
    assert images * 0.0567 < bat < images * 0.0567 + 0.15
    plate = generate.estimate_cost(
        [generate.plan_plate(shot, 3)], "gemini-3.1-flash-image", "4K", False
    )
    assert plate == pytest.approx(3 * 0.151, abs=0.01)


def test_largest_tier_and_unknown_price():
    assert generate.largest_tier("gemini-nano-banana-2.1") == "4K"
    with pytest.raises(SystemExit):
        generate.price("gemini-nano-banana-2.1", "8K", False)


def test_next_chain_steps_waits_on_review(shot):
    m = _empty()
    todo, waiting = generate.next_chain_steps(shot, 3, m)
    assert not todo and waiting[0].startswith("plate")

    m["accepted"]["plate"] = "example/plate/p.png"
    todo, _ = generate.next_chain_steps(shot, 3, m)
    assert [r.level for r in todo] == [5]

    m["objects"]["example/composites/level_5/c0.png"] = {}
    todo, waiting = generate.next_chain_steps(shot, 3, m)
    assert not todo and waiting == ["level_5 (review candidates, then accept)"]

    m["accepted"]["level_5"] = "example/composites/level_5/c0.png"
    todo, _ = generate.next_chain_steps(shot, 3, m)
    assert [r.level for r in todo] == [4, 6]

    m["accepted"]["level_4"] = "example/composites/level_4/x.png"
    m["objects"]["example/composites/level_6/y.png"] = {}
    todo, waiting = generate.next_chain_steps(shot, 3, m)
    assert [r.level for r in todo] == [3]
    assert waiting == ["level_6 (review candidates, then accept)"]


def test_live_run_without_a_key_fails_cleanly(env):
    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        generate.main(["plate", "example", "-n", "1"])


def _png(w=64, h=48):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_sync_run_saves_records_and_accepts(env, monkeypatch, shot):
    pytest.importorskip("google.genai")
    calls = []

    def fake_generate(model, contents, config):
        calls.append((model, contents, config))
        part = SimpleNamespace(
            inline_data=SimpleNamespace(data=_png(), mime_type="image/png")
        )
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
            usage_metadata=SimpleNamespace(prompt_token_count=1000),
            text=None,
        )

    client = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate))
    monkeypatch.setattr(generate, "_client", lambda: client)
    assert generate.main(["plate", "example", "-n", "2"]) == 0
    assert len(calls) == 2
    model, _, config = calls[0]
    assert model == "gemini-nano-banana-2.1"
    assert config["image_config"] == {"aspect_ratio": "4:3", "image_size": "4K"}

    manifest = masters.load_manifest("example")
    keys = sorted(manifest["objects"])
    assert len(keys) == 2 and all(k.startswith("example/plate/plate_") for k in keys)
    entry = manifest["objects"][keys[0]]
    assert entry["size"] == [64, 48] and entry["resolution_tier"] == "4K"
    assert entry["cost_usd"] == pytest.approx(0.113 + 1000 * 1.5e-6, abs=1e-4)

    generate.main(["accept", "example", "plate", keys[0].rsplit("/", 1)[1]])
    assert masters.accepted("example", "plate") == keys[0]

    # The anchor request now carries the accepted plate as its one image.
    calls.clear()
    assert generate.main(["chain", "example", "-n", "1"]) == 0
    ((_, contents, _),) = calls
    parts = contents[0].parts
    assert parts[0].inline_data.mime_type == "image/jpeg"
    assert "place the patient" in parts[-1].text
