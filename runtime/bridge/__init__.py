"""
BRIDGE's own runtime code: the game engine and the wire contract it speaks.

Container-only — this package ships into the AgentCore image via
`runtime/Dockerfile.voice` and is never installed as a dependency of `api/`
(`runtime/pyproject.toml` packages `voice_kit` alone).
"""

from .events import (
    EVENT_MODELS,
    ActionDetected,
    GameEvent,
    GameOver,
    StateUpdate,
    Timer,
    TranscriptUpdate,
)

__all__ = [
    "EVENT_MODELS",
    "ActionDetected",
    "GameEvent",
    "GameOver",
    "StateUpdate",
    "Timer",
    "TranscriptUpdate",
]
