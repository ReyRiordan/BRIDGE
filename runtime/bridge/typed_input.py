"""
Typed student turns (Text mode): the browser -> runtime half of the data channel.

A typed message arrives as an app message on the WebRTC data channel
(``StudentText`` in ``events.py``) and joins the pipeline exactly where STT
output does: a user ``TranscriptMessageFrame``, queued at the pipeline source
with ``PipelineWorker.queue_frame``. VAD and STT forward frames they do not
own, so the referee -> patient -> TTS -> sink path, the event ordering and the
session record are the same code as a spoken turn. Only ``source="text"``
differs, and that is what the record stores.

The runtime does not know a session's mode, so it accepts typed text from any
session. Once the game is over nothing is scored and the patient says nothing
more, so the turn skips the pipeline: it is recorded and echoed to the
transcript, which is all the referee would have done with it.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from pydantic import ValidationError
from voice_kit.processors import TranscriptMessageFrame
from voice_kit.types import TranscriptMessage

from .events import StudentText
from .session import STATUS_ACTIVE

logger = logging.getLogger(__name__)

MAX_STUDENT_TEXT_CHARS = 1000


def parse_student_text(message: Any) -> Optional[str]:
    """The stripped content of a valid ``student_text`` message, else None.

    Rejects anything that is not a dict, not ``type: "student_text"``, not
    ``v: 1``, empty after stripping, or over ``MAX_STUDENT_TEXT_CHARS``. pipecat
    consumes its own ``type: "signalling"`` messages before they get here, but
    those (and any other app message) are rejected all the same.
    """
    if not isinstance(message, dict) or message.get("type") != "student_text":
        return None
    try:
        parsed = StudentText.model_validate(message)
    except ValidationError:
        return None
    content = parsed.content.strip()
    if not content or len(content) > MAX_STUDENT_TEXT_CHARS:
        return None
    return content


async def inject_student_text(task, session, content: str, events=None) -> None:
    """Enter one typed turn into the session's pipeline.

    Active game: queue a user ``TranscriptMessageFrame`` with ``source="text"``
    at the pipeline source. Ended game: record the turn and emit the student's
    ``transcript_update`` (``events``, a ``GameEvents``), with nothing queued.
    """
    message = TranscriptMessage(
        role="user",
        content=content,
        timestamp=datetime.utcnow(),
        source="text",
    )
    if session.status == STATUS_ACTIVE:
        await task.queue_frame(TranscriptMessageFrame(message))
        return

    # The same three effects a post-game turn has on the voice path: the
    # referee echoes it and records it unscored, and the sink keeps it.
    if events is not None:
        events.transcript("student", message.content, message.timestamp)
    session.transcript.append(message)
    if session.recorder:
        session.recorder.student_turn(session, message, [])
