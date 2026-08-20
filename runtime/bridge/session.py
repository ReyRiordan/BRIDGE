"""
Per-session game state and the process-wide session registry.

One :class:`GameSession` per ``session_id``, held in memory for the life of the
container. That is the whole persistence story: AgentCore gives us session
affinity (one container per session), the simulation is a single sitting, and a
DB would buy nothing a warm container does not already provide.

The registry mirrors ``voice_kit.runtime._pipeline_tasks``: a module-level dict,
single asyncio loop, **no locks** — nothing here is touched from a worker
thread, so there is no shared state to guard. A pipeline rebuild on the same warm container therefore resumes the same
session — escalation, action states, clock origin and transcript — including a
session that already ended, so a reconnect after game over shows the terminal
state instead of silently restarting.

Every bound (start/goal/max escalation, time limit) is read from the scenario
JSON. No point value is hardcoded here.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from voice_kit.types import TranscriptMessage

from .events import StateUpdate

logger = logging.getLogger(__name__)

# Status vocabulary shared with the SPA: a game is `active` until it is `success`
# or `fail`. (The legacy app's extra `idle` state is gone — a session exists only
# once play has started.)
STATUS_ACTIVE = "active"
STATUS_SUCCESS = "success"
STATUS_FAIL = "fail"


@dataclass
class GameSession:
    """Authoritative state for one training session."""

    session_id: str
    scenario: dict
    # Seeded from the scenario's point bar in __post_init__.
    escalation: Optional[int] = None
    status: str = STATUS_ACTIVE
    # action type -> currently lit. Dict order (and so `active_actions` order) is
    # first-seen order: the transient types are all seeded by
    # `clear_transient_actions`, persisting ones on the turn they are earned.
    action_states: Dict[str, bool] = field(default_factory=dict)
    # Every action type detected at least once this session (for the debrief).
    actions_ever_taken: List[str] = field(default_factory=list)
    transcript: List[TranscriptMessage] = field(default_factory=list)
    # monotonic, not wall clock: container-lifetime-stable and NTP-immune, and it
    # survives a pipeline rebuild so a reconnect resumes a continuous clock.
    started_at: float = field(default_factory=time.monotonic)
    ended_at: Optional[float] = None

    def __post_init__(self) -> None:
        if self.escalation is None:
            self.escalation = self.point_bar["start"]

    # --- scenario-derived bounds ------------------------------------------

    @property
    def point_bar(self) -> dict:
        return self.scenario["point_bar"]

    @property
    def max_escalation(self) -> int:
        return self.point_bar["max"]

    @property
    def goal(self) -> int:
        return self.point_bar["goal"]

    @property
    def time_limit(self) -> int:
        return self.scenario["time_limit"]

    @property
    def action_map(self) -> Dict[str, dict]:
        return {a["type"]: a for a in self.scenario["actions"]}

    # --- turn mechanics ---------------------------------------------------

    def clear_transient_actions(self) -> None:
        """Unlight every non-persisting action, before this turn is scored.

        Called before applying, so an action the student re-earns this turn stays
        lit rather than flickering off.
        """
        for action in self.scenario["actions"]:
            if not action.get("persist"):
                self.action_states[action["type"]] = False

    def apply_action(self, action_type: str) -> Optional[dict]:
        """Apply one detected action; return the scenario action, or None if unknown.

        The point value comes from the SCENARIO, never from the model — the
        referee reports *what* happened, the server decides what it costs.
        """
        action = self.action_map.get(action_type)
        if action is None:
            return None
        self.escalation = max(
            0, min(self.max_escalation, self.escalation + action["point_change"])
        )
        self.action_states[action_type] = True
        if action_type not in self.actions_ever_taken:
            self.actions_ever_taken.append(action_type)
        return action

    def active_actions(self) -> List[str]:
        """Currently lit action types, in first-seen order."""
        return [t for t, lit in self.action_states.items() if lit]

    # --- terminal transitions ---------------------------------------------

    def check_terminal(self) -> Optional[Tuple[str, str]]:
        """Return ``(status, reason)`` if this session just ended, else None.

        Success is checked first: an utterance that lands exactly on the goal wins
        even when the bar is also at its maximum in a degenerate scenario config.
        """
        if self.status != STATUS_ACTIVE:
            return None
        if self.escalation <= self.goal:
            return self._end(STATUS_SUCCESS, "Escalation reduced to goal")
        if self.escalation >= self.max_escalation:
            return self._end(STATUS_FAIL, "Escalation reached maximum")
        return None

    def expire(self) -> Tuple[str, str]:
        """End the session on the clock (idempotent for an already-ended one)."""
        if self.status != STATUS_ACTIVE:
            return self.status, "Time limit reached"
        return self._end(STATUS_FAIL, "Time limit reached")

    def _end(self, status: str, reason: str) -> Tuple[str, str]:
        self.status = status
        self.ended_at = time.monotonic()
        logger.info("[%s] game over: %s (%s)", self.session_id, status, reason)
        return status, reason

    # --- projections ------------------------------------------------------

    def elapsed_seconds(self) -> int:
        return int(time.monotonic() - self.started_at)

    def to_state_update(self) -> StateUpdate:
        return StateUpdate(
            escalation=self.escalation,
            max=self.max_escalation,
            active_actions=self.active_actions(),
            status=self.status,
        )


# --- registry -------------------------------------------------------------

# session_id -> GameSession, for the container's life. Shared in-process state,
# the same reason Dockerfile.voice runs uvicorn with --workers 1.
_sessions: Dict[str, GameSession] = {}


def get_or_create_session(session_id: str, scenario: dict) -> GameSession:
    """Return the live session for this id, creating one on first connect.

    An existing session is REUSED even when it is terminal: a reconnect after
    game over must show the ending, never restart the game.
    """
    _sweep(time.monotonic())
    session = _sessions.get(session_id)
    if session is not None:
        logger.info(
            "Resuming game session %s (escalation=%s, status=%s)",
            session_id,
            session.escalation,
            session.status,
        )
        return session
    session = GameSession(session_id=session_id, scenario=scenario)
    _sessions[session_id] = session
    logger.info("Created game session %s", session_id)
    return session


def get_session(session_id: str) -> Optional[GameSession]:
    return _sessions.get(session_id)


def drop_session(session_id: str) -> Optional[GameSession]:
    return _sessions.pop(session_id, None)


def _sweep(now: float) -> None:
    """Evict sessions nobody can reconnect to.

    Without this the dict grows for the container's whole ``maxLifetime``. Two
    rules: a finished session outlives its grace window, and any session older
    than twice the scenario's time limit cannot still be in play.
    """
    from .config import GAME_GRACE_SECONDS

    for session_id, session in list(_sessions.items()):
        ended = (
            session.ended_at is not None and now - session.ended_at > GAME_GRACE_SECONDS
        )
        aged_out = now - session.started_at > 2 * session.time_limit
        if ended or aged_out:
            _sessions.pop(session_id, None)
            logger.info("Swept game session %s", session_id)
