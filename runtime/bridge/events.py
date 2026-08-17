"""
The frozen v1 data-channel event envelope.

Every message the runtime pushes to the browser over the WebRTC data channel is
one of these models, serialized with `model_dump()`. Two properties are load
bearing:

- ``v`` — envelope version, always 1. A future breaking change bumps it so an
  old SPA can reject rather than misread.
- ``type`` — the pydantic discriminator for ``GameEvent`` and the field the
  frontend switches on.

This module is the SINGLE source of truth for the contract: the TypeScript in
``web/src/voice/gameEvents.gen.ts`` is generated from ``EVENT_MODELS`` by
``scripts/gen_event_types.py`` and CI fails when the two drift.

Imports nothing from pipecat or voice_kit — it is a pure schema module usable
from the control plane, the runtime and the tests alike.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class TranscriptUpdate(BaseModel):
    """One finalized conversational turn, pushed as it happens."""

    # Domain roles, not chat roles: the UI labels speakers by who they are in
    # the simulation. (voice_kit.types.TranscriptMessage's user/assistant pair
    # is the LLM-facing shape and is mapped to these at the boundary.)
    v: Literal[1] = 1
    type: Literal["transcript_update"] = "transcript_update"
    role: Literal["student", "patient"]
    content: str
    timestamp: str


class StateUpdate(BaseModel):
    """The authoritative game state after a turn was refereed."""

    v: Literal[1] = 1
    type: Literal["state_update"] = "state_update"
    escalation: int
    max: int
    active_actions: list[str]
    status: str


class ActionDetected(BaseModel):
    """A de-escalation (or escalating) action the referee scored this turn."""

    v: Literal[1] = 1
    type: Literal["action_detected"] = "action_detected"
    action_type: str
    desc: str
    point_change: int


class Timer(BaseModel):
    """Session clock, in seconds."""

    v: Literal[1] = 1
    type: Literal["timer"] = "timer"
    elapsed: int
    limit: int


class GameOver(BaseModel):
    """Terminal event: escalation hit 0 (success) or 10 / time ran out (fail)."""

    v: Literal[1] = 1
    type: Literal["game_over"] = "game_over"
    status: Literal["success", "fail"]
    reason: str


GameEvent = Annotated[
    Union[TranscriptUpdate, StateUpdate, ActionDetected, Timer, GameOver],
    Field(discriminator="type"),
]

# Registry consumed by scripts/gen_event_types.py — order defines the order of
# the generated interfaces and of the GameEvent union.
EVENT_MODELS = [
    TranscriptUpdate,
    StateUpdate,
    ActionDetected,
    Timer,
    GameOver,
]

__all__ = [
    "TranscriptUpdate",
    "StateUpdate",
    "ActionDetected",
    "Timer",
    "GameOver",
    "GameEvent",
    "EVENT_MODELS",
]
