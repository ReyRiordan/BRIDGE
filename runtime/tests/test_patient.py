"""
The patient agent: prompt assembly, the voice mapping, the per-turn marker.

The assembly assertions are the enforcement of one design decision — the case
file's ``locked_information`` must never reach the model, because history-taking
is out of scope for the current sim.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.config import (  # noqa: E402
    load_patient_case,
    load_patient_prompt_template,
    load_scenario,
)
from bridge.patient import (  # noqa: E402
    build_patient_prompt,
    build_voice_config,
    turn_context,
)
from bridge.session import GameSession  # noqa: E402

SCENARIO = load_scenario()


def test_voice_config_comes_from_the_scenario_speech_block():
    voice = build_voice_config(SCENARIO)
    assert voice.provider == "inworld"
    assert voice.voice == "Mark"
    assert voice.model == "inworld-tts-1.5-mini"
    assert voice.speed == 1.2


def test_voice_config_defaults_speed_to_one():
    scenario = {"speech": {"provider": "polly", "voice": "Ruth"}}
    voice = build_voice_config(scenario)
    assert voice.model is None
    assert voice.speed == 1.0


CASE = load_patient_case()
PROMPT = build_patient_prompt(SCENARIO)


def test_loaders_read_the_configured_paths(tmp_path, monkeypatch):
    template = tmp_path / "p.txt"
    template.write_text("hello {patient_name}", encoding="utf-8")
    case = tmp_path / "p.json"
    case.write_text('{"demographics": {"name": "Sam"}}', encoding="utf-8")
    assert load_patient_prompt_template(str(template)) == "hello {patient_name}"
    assert load_patient_case(str(case))["demographics"]["name"] == "Sam"


def test_prompt_substitutes_the_patient_name():
    assert "{patient_name}" not in PROMPT
    assert CASE["demographics"]["name"] in PROMPT


def test_prompt_renders_the_case_sections():
    assert "=== PATIENT CASE DETAILS ===" in PROMPT
    for tag in ("demographics", "behavior", "chief_concern", "free_information"):
        assert f"<{tag}>" in PROMPT and f"</{tag}>" in PROMPT
    for item in CASE["free_information"]:
        assert item in PROMPT


def test_prompt_never_exposes_locked_information():
    # The strongest possible guarantee against leaking history: the model is
    # never shown the items at all.
    assert "locked_information" not in PROMPT
    for item in CASE["locked_information"]:
        assert item not in PROMPT


def test_prompt_documents_the_per_turn_marker():
    # The template must describe the marker turn_context actually injects.
    assert "[CURRENT ESCALATION:" in PROMPT


def test_prompt_accepts_template_and_case_overrides():
    prompt = build_patient_prompt(
        SCENARIO,
        template="I am {patient_name}.",
        case={
            "demographics": {
                "name": "Sam",
                "date_of_birth": "2000-01-01",
                "sex": "female",
                "gender": "female",
                "background": "test",
            },
            "chief_concern": "Headache",
            "free_information": ["Head hurts"],
        },
    )
    assert prompt.startswith("I am Sam.")
    # `behavior` is optional — an absent key renders no block.
    assert "<behavior>" not in prompt
    assert "Head hurts" in prompt


def test_turn_context_format():
    session = GameSession(session_id="s1", scenario=SCENARIO)
    assert turn_context(session) == "[CURRENT ESCALATION: 5/10]"
    session.apply_action("Environmental")
    assert turn_context(session) == "[CURRENT ESCALATION: 3/10]"
