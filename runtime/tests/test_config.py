"""
The BRIDGE_LOCAL umbrella flag: what it implies, and the two things it refuses
(a production ENV, and any AWS-backed provider).

EVERY case constructs settings with ``_env_file=None``. The settings class now
reads a repo-root ``.env`` as well as the package-adjacent one, and a developer's
real ``.env`` would otherwise leak provider values into these assertions (CI is
unaffected — a fresh checkout has no ``.env``).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit.config import VoiceKitSettings  # noqa: E402

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
