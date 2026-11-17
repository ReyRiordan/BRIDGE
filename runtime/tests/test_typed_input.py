"""Typed student turns: parsing the inbound message and entering it into the pipeline."""

import asyncio
import json

import pytest
from bridge.config import load_scenario
from bridge.emitter import GameEvents
from bridge.session import GameSession
from bridge.typed_input import (
    MAX_STUDENT_TEXT_CHARS,
    inject_student_text,
    parse_student_text,
)
from voice_kit.processors import TranscriptMessageFrame


def msg(content="I hear you.", **overrides):
    return {"v": 1, "type": "student_text", "content": content, **overrides}


# --- parse ------------------------------------------------------------------


def test_parse_accepts_a_valid_message_and_strips_it():
    assert parse_student_text(msg("  I hear you.\n")) == "I hear you."


def test_parse_accepts_exactly_the_cap():
    assert parse_student_text(msg("a" * MAX_STUDENT_TEXT_CHARS)) is not None


@pytest.mark.parametrize(
    "message",
    [
        msg(""),
        msg("   \n\t "),
        msg("a" * (MAX_STUDENT_TEXT_CHARS + 1)),
        msg(v=2),
        msg(type="transcript_update"),
        {"v": 1, "type": "student_text"},  # no content
        msg(content=42),
        "student_text",
        ["student_text"],
        None,
        # pipecat consumes these itself; if one ever leaks through, it is not text.
        {"type": "signalling", "message": {"type": "trackStatus"}},
    ],
    ids=[
        "empty",
        "whitespace",
        "over-long",
        "v2",
        "wrong-type",
        "no-content",
        "non-string",
        "string",
        "list",
        "none",
        "signalling",
    ],
)
def test_parse_rejects(message):
    assert parse_student_text(message) is None


# --- inject -----------------------------------------------------------------


class FakeTask:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


class FakeRecorder:
    def __init__(self):
        self.turns = []

    def student_turn(self, session, message, applied):
        self.turns.append((message, applied))


def a_session():
    session = GameSession("scenario_1--s1", load_scenario("scenario_1"))
    session.recorder = FakeRecorder()
    return session


def test_inject_queues_a_text_user_frame_on_an_active_session():
    task, session = FakeTask(), a_session()

    asyncio.run(inject_student_text(task, session, "Can I get you some water?"))

    (frame,) = task.frames
    assert isinstance(frame, TranscriptMessageFrame)
    assert frame.message.role == "user"
    assert frame.message.source == "text"
    assert frame.message.content == "Can I get you some water?"
    # The referee records it once the frame reaches it; nothing records it here.
    assert session.recorder.turns == []


def test_inject_after_game_over_records_and_echoes_without_queueing():
    task, session = FakeTask(), a_session()
    session.status = "fail"
    sent = []
    events = GameEvents(session.session_id, lambda p: sent.append(json.loads(p)))

    asyncio.run(inject_student_text(task, session, "sorry", events))

    assert task.frames == []
    ((message, applied),) = session.recorder.turns
    assert message.source == "text" and message.content == "sorry"
    assert applied == []
    assert [m.content for m in session.transcript] == ["sorry"]
    assert sent == [
        {
            "v": 1,
            "type": "transcript_update",
            "role": "student",
            "content": "sorry",
            "timestamp": message.timestamp.isoformat(),
        }
    ]
