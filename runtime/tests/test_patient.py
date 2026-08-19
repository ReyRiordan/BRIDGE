"""
The patient agent's seams: the voice mapping and the per-turn context marker.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.config import load_scenario  # noqa: E402
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


def test_patient_prompt_is_a_non_empty_placeholder():
    # [Rewrite E] replaces the body; the seam is what is under test.
    assert build_patient_prompt(SCENARIO).strip()


def test_turn_context_format():
    session = GameSession(session_id="s1", scenario=SCENARIO)
    assert turn_context(session) == "[CURRENT ESCALATION: 5/10]"
    session.apply_action("Environmental")
    assert turn_context(session) == "[CURRENT ESCALATION: 3/10]"
