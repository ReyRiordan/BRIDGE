"""
The BRIDGE_LOCAL umbrella flag: what it implies, and the two things it refuses
(a production ENV, and any AWS-backed provider). Plus ``bridge.config``'s
scenario allow-list, session-id parsing and per-scenario loaders.

EVERY case constructs settings with ``_env_file=None``. The settings class now
reads a repo-root ``.env`` as well as the package-adjacent one, and a developer's
real ``.env`` would otherwise leak provider values into these assertions (CI is
unaffected — a fresh checkout has no ``.env``).
"""

import pytest
from bridge import config as bridge_config
from voice_kit.config import VoiceKitSettings

# The zero-AWS provider trio local mode requires (legacy parity, minus Bedrock).
LOCAL_PROVIDERS = {
    "stt_provider": "together",
    "tts_provider": "inworld",
    "llm_provider": "openrouter",
}


def _settings(**overrides) -> VoiceKitSettings:
    return VoiceKitSettings(_env_file=None, **{**LOCAL_PROVIDERS, **overrides})


def test_bridge_local_implies_the_local_invoker():
    assert _settings(bridge_local=True).voice_invoker == "local"


def test_an_explicit_voice_invoker_still_wins_under_the_flag():
    # Fine-grained selector: a local control plane pointed at a deployed runtime.
    assert (
        _settings(bridge_local=True, voice_invoker="agentcore").voice_invoker
        == "agentcore"
    )


def test_production_refuses_local_mode():
    with pytest.raises(ValueError, match="ENV=production"):
        _settings(bridge_local=True, env="production")


@pytest.mark.parametrize(
    "field,value",
    [
        ("stt_provider", "transcribe"),
        ("tts_provider", "polly"),
        ("llm_provider", "bedrock"),
    ],
)
def test_aws_backed_providers_are_refused(field, value):
    with pytest.raises(ValueError, match=field.upper()):
        _settings(bridge_local=True, **{field: value})


def test_bedrock_referee_is_refused(monkeypatch):
    # REFEREE_PROVIDER belongs to bridge.config (container-only), so the guard
    # reads os.environ rather than importing bridge into voice_kit.
    monkeypatch.setenv("REFEREE_PROVIDER", "bedrock")
    with pytest.raises(ValueError, match="REFEREE_PROVIDER"):
        _settings(bridge_local=True)


def test_the_local_provider_trio_passes(monkeypatch):
    monkeypatch.setenv("REFEREE_PROVIDER", "openrouter")
    settings = _settings(bridge_local=True)

    assert settings.stt_provider == "together"
    assert settings.tts_provider == "inworld"
    assert settings.llm_provider == "openrouter"


def test_the_flag_off_touches_nothing():
    settings = VoiceKitSettings(
        _env_file=None,
        env="production",
        stt_provider="transcribe",
        tts_provider="polly",
        llm_provider="bedrock",
    )

    assert settings.bridge_local is False
    assert settings.voice_invoker == "agentcore"


def test_voice_runtime_url_defaults_to_the_local_runtime_port():
    assert VoiceKitSettings(_env_file=None).voice_runtime_url == "http://localhost:8080"


def test_vad_defaults_reproduce_pipecat():
    """Pinned at pipecat 1.3.0's own values, so an unset env changes nothing."""
    settings = _settings()

    assert settings.vad_confidence == 0.7
    assert settings.vad_start_secs == 0.2
    assert settings.vad_stop_secs == 0.2
    assert settings.vad_min_volume == 0.6
    assert settings.vad_speech_activity_period == 0.2
    assert settings.vad_audio_idle_timeout == 1.0


@pytest.mark.parametrize(
    "field,value",
    [
        ("vad_confidence", 1.5),
        ("vad_confidence", -0.1),
        ("vad_min_volume", 1.5),
        ("vad_start_secs", -1),
        ("vad_stop_secs", -1),
        ("vad_speech_activity_period", -1),
        ("vad_audio_idle_timeout", -1),
    ],
)
def test_out_of_range_vad_values_are_refused(field, value):
    with pytest.raises(ValueError, match=field):
        _settings(**{field: value})


# --- bridge.config: the scenario allow-list and the per-scenario loaders ------


def test_the_allow_list_is_the_scenario_folders():
    assert bridge_config.scenario_ids() == ["scenario_1", "scenario_2", "scenario_3"]


@pytest.mark.parametrize(
    "session_id, scenario_id",
    [
        ("scenario_1--0f8e", "scenario_1"),
        ("scenario_3--3b1c-4d2e-9a7f", "scenario_3"),
        # Split on the FIRST separator: the rest of the id is opaque.
        ("scenario_2--a--b", "scenario_2"),
    ],
)
def test_parse_session_id_returns_the_prefix(session_id, scenario_id):
    assert bridge_config.parse_session_id(session_id) == scenario_id


@pytest.mark.parametrize(
    "session_id", ["0f8e-uuid", "scenario_1", "scenario_1-x", "scenario_4--x", "--x"]
)
def test_parse_session_id_rejects_a_missing_or_unknown_prefix(session_id):
    with pytest.raises(ValueError, match="known scenario id"):
        bridge_config.parse_session_id(session_id)


@pytest.mark.parametrize("scenario_id", ["scenario_1", "scenario_2", "scenario_3"])
def test_each_scenario_loads_its_own_files(scenario_id):
    scenario = bridge_config.load_scenario(scenario_id)
    assert scenario["id"] == scenario_id
    assert scenario["title"]
    assert bridge_config.load_patient_prompt_template(scenario_id).strip()
    assert bridge_config.load_patient_case(scenario_id)["demographics"]["name"]
    # Cached by id: the same object comes back.
    assert bridge_config.load_scenario(scenario_id) is scenario


def test_the_loaders_refuse_an_unknown_scenario():
    for loader in (
        bridge_config.load_scenario,
        bridge_config.load_patient_prompt_template,
        bridge_config.load_patient_case,
    ):
        with pytest.raises(ValueError, match="unknown scenario"):
            loader("../resources")


@pytest.mark.parametrize("scenario_id", ["scenario_1", "scenario_2", "scenario_3"])
def test_every_requires_names_a_real_action_and_every_end_has_a_debrief(scenario_id):
    actions = bridge_config.load_scenario(scenario_id)["actions"]
    types = {a["type"] for a in actions}
    for action in actions:
        if "requires" in action:
            assert action["requires"] in types
            assert "requires" not in next(
                a for a in actions if a["type"] == action["requires"]
            )
            assert isinstance(action["point_change_without"], int)
        if action.get("ends_simulation"):
            assert action["point_change"] == 0
            assert action["debrief"].strip()
