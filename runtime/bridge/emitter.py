"""
The data-channel emitter: turning game state into v1 envelope messages.

Kept OUT of ``events.py`` on purpose. That module is the frozen wire contract
(and the source the TypeScript is generated from), so behavior changes must
never show up as diffs that read like contract changes.

Emission is best-effort by design. The data channel is not open when the
pipeline is built, and it goes away the moment the student closes the tab —
neither is a reason to kill a turn, so every send swallows its failure. The
first one logs at warning, the rest at debug, so a dead channel does not fill
the log with one line per timer tick.
"""

import logging
from typing import Callable, Optional

from voice_kit.types import TranscriptMessage

from .events import ActionDetected, GameOver, Timer, TranscriptUpdate

logger = logging.getLogger(__name__)


class GameEvents:
    """Sends v1 game events over one session's WebRTC data channel."""

    def __init__(self, session_id: str, emit: Optional[Callable[[str], None]] = None):
        self.session_id = session_id
        self._emit = emit
        self._warned = False

    def send(self, model) -> None:
        if self._emit is None:
            return
        try:
            self._emit(model.model_dump_json())
        except Exception as e:
            if self._warned:
                logger.debug("[%s] data-channel emit failed: %s", self.session_id, e)
            else:
                self._warned = True
                logger.warning("[%s] data-channel emit failed: %s", self.session_id, e)

    # --- helpers ----------------------------------------------------------

    def transcript(self, role: str, content: str, timestamp) -> None:
        # The envelope field is a string; TranscriptMessage carries a datetime.
        self.send(
            TranscriptUpdate(
                role=role,
                content=content,
                timestamp=timestamp.isoformat()
                if hasattr(timestamp, "isoformat")
                else str(timestamp),
            )
        )

    def action(self, scenario_action: dict) -> None:
        self.send(
            ActionDetected(
                action_type=scenario_action["type"],
                desc=scenario_action["desc"],
                point_change=scenario_action["point_change"],
            )
        )

    def state(self, session) -> None:
        self.send(session.to_state_update())

    def timer(self, elapsed: int, limit: int) -> None:
        self.send(Timer(elapsed=elapsed, limit=limit))

    def game_over(self, status: str, reason: str) -> None:
        self.send(GameOver(status=status, reason=reason))


def transcript_event(message: TranscriptMessage) -> Optional[str]:
    """Sink mapper for ``EventSinkProcessor``: patient turns only.

    The student's utterance is emitted by the referee *before* scoring (so the UI
    never looks frozen), which is far upstream of the sink; re-emitting it here
    would duplicate it and land it after the whole turn's events.
    """
    if message.role != "assistant":
        return None
    return TranscriptUpdate(
        role="patient",
        content=message.content,
        timestamp=message.timestamp.isoformat(),
    ).model_dump_json()
