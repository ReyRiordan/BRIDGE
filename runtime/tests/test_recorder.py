"""
The session recorder: ordering, fail-open, the bounded queue, the drain on
close, and the payloads each game event produces.

Every table here is a fake that records (or blocks, or raises). The point of the
hung-table test is the recorder's core promise: a stuck store never delays a
turn.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime

from bridge.config import load_scenario
from bridge.emitter import GameEvents
from bridge.recorder import SessionRecorder
from bridge.referee import RefereeProcessor
from bridge.session import GameSession
from voice_kit.providers.llm import BaseLLM
from voice_kit.types import TranscriptMessage

SCENARIO = load_scenario("scenario_1")
SID = "scenario_1--abc"


class RecordingTable:
    def __init__(self, fail_on=()):
        self.updates = []
        self._fail_on = set(fail_on)

    def update(self, session_id, update):
        index = len(self.updates)
        self.updates.append(update)
        if index in self._fail_on:
            raise RuntimeError("boom")


class BlockingTable:
    def __init__(self):
        self.release = threading.Event()
        self.calls = 0

    def update(self, session_id, update):
        self.calls += 1
        self.release.wait(5)


class StubLLM(BaseLLM):
    def __init__(self, *types):
        self._reply = json.dumps({"detected_actions": [{"type": t} for t in types]})

    async def chat(self, messages, system_prompt, response_format=None):
        return self._reply


def a_session(table, **kwargs):
    session = GameSession(session_id=SID, scenario=SCENARIO, **kwargs)
    session.recorder = SessionRecorder(SID, table)
    return session


def a_referee(session, *types):
    return RefereeProcessor(
        session=session,
        events=GameEvents(SID, lambda payload: None),
        llm=StubLLM(*types),
        system_prompt="referee",
    )


def said(content, source="voice"):
    return TranscriptMessage(
        role="user", content=content, timestamp=datetime(2026, 1, 1), source=source
    )


def test_updates_apply_in_order():
    table = RecordingTable()

    async def run():
        session = a_session(table)
        session.recorder.started(session)
        for i in range(5):
            session.recorder.patient_turn(
                session,
                TranscriptMessage(
                    role="assistant", content=f"p{i}", timestamp=datetime(2026, 1, 1)
                ),
            )
        await session.recorder.close()

    asyncio.run(run())
    assert table.updates[0]["set"]["status"] == "active"
    assert [u["append"]["turns"][0]["content"] for u in table.updates[1:]] == [
        "p0",
        "p1",
        "p2",
        "p3",
        "p4",
    ]


def test_failed_write_is_logged_and_dropped(caplog):
    table = RecordingTable(fail_on={0})

    async def run():
        session = a_session(table)
        session.recorder.started(session)
        session.recorder.inactivity(session)
        await session.recorder.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
    assert len(table.updates) == 2  # the failure did not stop the next write
    assert "write failed" in caplog.text


def test_hung_table_does_not_block_score_turn():
    table = BlockingTable()

    async def run():
        session = a_session(table)
        referee = a_referee(session, "Environmental")
        session.recorder.started(session)
        await asyncio.sleep(0)  # the writer is now stuck inside the table
        await asyncio.wait_for(referee.score_turn(said("dim the lights")), 1)
        await asyncio.wait_for(referee.score_turn(said("and again")), 1)
        await asyncio.wait_for(session.recorder.close(timeout=0.1), 1)
        # Unstick the worker thread before asyncio.run joins the executor.
        table.release.set()
        return session

    try:
        session = asyncio.run(run())
    finally:
        table.release.set()
    assert session.escalation == 3
    assert table.calls == 1


def test_overflow_drops_with_warning(caplog):
    table = RecordingTable()

    async def run():
        session = GameSession(session_id=SID, scenario=SCENARIO)
        recorder = SessionRecorder(SID, table, maxsize=2)
        # No await between the calls: the writer cannot drain yet.
        recorder.inactivity(session)
        recorder.inactivity(session)
        recorder.inactivity(session)
        await recorder.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
    assert len(table.updates) == 2
    assert "queue full" in caplog.text


def test_close_drains():
    table = RecordingTable()

    async def run():
        session = a_session(table)
        session.recorder.started(session)
        session.recorder.inactivity(session)
        session.recorder.abandoned(session)
        await session.recorder.close()
        # Closed: a late event is dropped rather than queued forever.
        session.recorder.inactivity(session)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert len(table.updates) == 3
    assert table.updates[-1]["set"]["status"] == "abandoned"
    assert table.updates[-1]["set"]["end_reason"] == "abandoned"


def test_student_turn_payload():
    table = RecordingTable()

    async def run():
        session = a_session(table)
        session.recorder.started(session)
        referee = a_referee(session, "Environmental")
        await referee.score_turn(said("dim the lights", source="text"))
        await referee.score_turn(said("dim them more"))  # a repeat: 0, repeat
        await session.recorder.close()

    asyncio.run(run())
    first, repeat = table.updates[1], table.updates[2]

    turn = first["append"]["turns"][0]
    assert turn["role"] == "student"
    assert turn["content"] == "dim the lights"
    assert turn["source"] == "text"
    assert turn["actions"] == [
        {"type": "Environmental", "point_change": -2, "repeat": False}
    ]
    assert turn["escalation_after"] == 3
    assert isinstance(turn["t"], float) and turn["at"].endswith("+00:00")
    assert first["add"] == {"helpful_count": 1}
    assert first["append"]["timeline"] == [
        {"t": turn["t"], "escalation": 3, "cause": "turn"}
    ]

    turn = repeat["append"]["turns"][0]
    assert turn["actions"] == [
        {"type": "Environmental", "point_change": 0, "repeat": True}
    ]
    assert repeat["add"] == {}
    # The escalation did not move, so no timeline point.
    assert repeat["append"]["timeline"] == []


def test_a_terminal_turn_records_the_ending_after_the_turn():
    table = RecordingTable()

    async def run():
        session = a_session(table, escalation=2)
        referee = a_referee(session, "Environmental")
        await referee.score_turn(said("dim the lights"))
        # After the game is over the student is still recorded, unscored.
        await referee.score_turn(said("thanks"))
        await session.recorder.close()

    asyncio.run(run())
    turn, end, after = table.updates
    assert turn["append"]["turns"][0]["escalation_after"] == 0
    assert end["set"]["status"] == "success"
    assert end["set"]["end_reason"] == "goal"
    assert end["set"]["reason"] == "Escalation reduced to goal"
    assert isinstance(end["set"]["duration_s"], float)
    assert after["append"]["turns"][0]["actions"] == []


def test_started_seeds_the_bounds_and_the_first_timeline_point():
    table = RecordingTable()

    async def run():
        session = a_session(table)
        session.recorder.started(session)
        session.recorder.started(session)  # a reconnect: ignored
        await session.recorder.close()

    asyncio.run(run())
    (start,) = table.updates
    assert start["set"]["escalation"] == 5
    assert start["set"]["max_escalation"] == 10
    assert start["set"]["goal"] == 0
    assert start["set"]["time_limit_s"] == 300
    assert start["append"]["timeline"] == [{"t": 0, "escalation": 5, "cause": "start"}]
