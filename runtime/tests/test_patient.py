"""
The patient agent: prompt assembly, the voice mapping, the per-turn marker.

The assembly assertions are the enforcement of one design decision — the case
file's ``locked_information`` must never reach the model, because history-taking
is out of scope for the current sim.
"""

import pytest
from bridge.config import (
    load_patient_case,
    load_scenario,
    scenario_ids,
)
from bridge.patient import (
    build_patient_prompt,
    build_voice_config,
    turn_context,
)
from bridge.session import GameSession

SCENARIO = load_scenario("scenario_1")


def test_voice_config_comes_from_the_scenario_speech_block():
    voice = build_voice_config(SCENARIO)
    assert voice.provider == "inworld"
    assert voice.voice == "Mark"
    assert voice.model == "inworld-tts-2-flash"
    assert voice.speed == 1.2


def test_voice_config_defaults_speed_to_one():
    scenario = {"speech": {"provider": "polly", "voice": "Ruth"}}
    voice = build_voice_config(scenario)
    assert voice.model is None
    assert voice.speed == 1.0


CASE = load_patient_case("scenario_1")
PROMPT = build_patient_prompt(SCENARIO)


def test_each_scenario_builds_its_own_persona():
    # The template is per scenario: scenario_1's hardcodes the emergency
    # department, which would be wrong for the clinic and the ward.
    prompts = {sid: build_patient_prompt(load_scenario(sid)) for sid in scenario_ids()}
    assert "emergency department" in prompts["scenario_1"]
    assert "family medicine clinic" in prompts["scenario_2"]
    assert "internal medicine service" in prompts["scenario_3"]
    assert len(set(prompts.values())) == len(prompts)


@pytest.mark.parametrize("scenario_id", ["scenario_1", "scenario_2", "scenario_3"])
def test_every_persona_keeps_the_standing_rules(scenario_id):
    case = load_patient_case(scenario_id)
    prompt = build_patient_prompt(load_scenario(scenario_id))
    assert "{patient_name}" not in prompt
    assert case["demographics"]["name"] in prompt
    assert "[CURRENT ESCALATION:" in prompt
    assert "NEVER give clinical history" in prompt
    for item in case["locked_information"]:
        assert item not in prompt


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
