"""The v1 data-channel envelope is a frozen cross-layer contract — pin it."""

import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.events import (  # noqa: E402  — needs the sys.path line above
    EVENT_MODELS,
    ActionDetected,
    GameEvent,
    GameOver,
    StateUpdate,
    Timer,
    TranscriptUpdate,
)

game_event = TypeAdapter(GameEvent)

SAMPLES = [
    (
        TranscriptUpdate,
        {
            "role": "student",
            "content": "I hear that you're frustrated.",
            "timestamp": "2026-01-01T00:00:00Z",
        },
    ),
    (
        StateUpdate,
        {
            "escalation": 4,
            "max": 10,
            "active_actions": ["remove_restraints"],
            "status": "in_progress",
        },
    ),
    (
        ActionDetected,
        {
            "action_type": "validate",
            "desc": "Validated the patient",
            "point_change": -1,
        },
    ),
    (Timer, {"elapsed": 30, "limit": 600}),
    (GameOver, {"status": "success", "reason": "Escalation reached zero"}),
]


def test_every_model_is_registered():
    assert EVENT_MODELS == [model for model, _ in SAMPLES]


@pytest.mark.parametrize(
    "model,payload", SAMPLES, ids=lambda a: getattr(a, "__name__", "")
)
def test_round_trip(model, payload):
    event = model(**payload)
    dumped = event.model_dump()

    assert dumped["v"] == 1
    assert dumped["type"] == model.model_fields["type"].default
    assert model(**dumped) == event


@pytest.mark.parametrize(
    "model,payload", SAMPLES, ids=lambda a: getattr(a, "__name__", "")
)
def test_union_discriminates_on_type(model, payload):
    dumped = model(**payload).model_dump()

    assert isinstance(game_event.validate_python(dumped), model)


def test_union_rejects_unknown_type():
    with pytest.raises(ValidationError):
        game_event.validate_python({"v": 1, "type": "nope", "elapsed": 0, "limit": 1})


def test_union_rejects_wrong_envelope_version():
    with pytest.raises(ValidationError):
        game_event.validate_python({"v": 2, "type": "timer", "elapsed": 0, "limit": 1})


def test_transcript_roles_are_domain_roles():
    """Deliberately student/patient — not voice_kit's chat-native user/assistant."""
    with pytest.raises(ValidationError):
        TranscriptUpdate(
            role="assistant", content="hi", timestamp="2026-01-01T00:00:00Z"
        )
