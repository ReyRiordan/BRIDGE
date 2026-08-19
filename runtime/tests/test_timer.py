"""
The 1 Hz session clock: tick cadence, expiry, and single-emitter restart.

``run_timer`` takes its sleep as an argument, so these run instantly.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import timer as timer_module  # noqa: E402
from bridge.config import load_scenario  # noqa: E402
from bridge.emitter import GameEvents  # noqa: E402
from bridge.session import GameSession  # noqa: E402
from bridge.timer import cancel_timer, run_timer, start_timer  # noqa: E402

SCENARIO = load_scenario()
LIMIT = SCENARIO["time_limit"]


@pytest.fixture(autouse=True)
def clean_timers():
    timer_module._timers.clear()
    yield
    timer_module._timers.clear()


class Clock:
    """A fake sleep that advances the session's monotonic origin instead of waiting."""

    def __init__(self, session):
        self.session = session
        self.sleeps = []

    async def __call__(self, seconds):
        self.sleeps.append(seconds)
        self.session.started_at -= seconds


def a_session() -> GameSession:
    return GameSession(session_id="s1", scenario=SCENARIO)


def recorded(session):
    sent = []
    events = GameEvents("s1", lambda payload: sent.append(json.loads(payload)))
    return events, sent


def test_ticks_once_a_second_until_the_session_ends():
    session = a_session()
    events, sent = recorded(session)
    clock = Clock(session)

    async def run():
        # Flip the session terminal after three ticks, as the referee would.
        async def sleep(seconds):
            await clock(seconds)
            if len(clock.sleeps) == 3:
                session.status = "success"

        await run_timer(session, events, sleep=sleep)

    asyncio.run(run())
    assert clock.sleeps == [1, 1, 1]
    assert [e["type"] for e in sent] == ["timer"] * 3
    assert [e["elapsed"] for e in sent] == [0, 1, 2]
    assert all(e["limit"] == LIMIT for e in sent)


def test_expiry_emits_game_over_and_calls_on_expire_once():
    session = a_session()
    events, sent = recorded(session)
    session.started_at -= LIMIT  # already at the limit
    expired = []

    async def run():
        await run_timer(
            session, events, on_expire=lambda: expired.append(1), sleep=Clock(session)
        )

    asyncio.run(run())
    assert [e["type"] for e in sent] == ["timer", "game_over"]
    assert sent[0]["elapsed"] == LIMIT  # clamped to the limit, never past it
    assert sent[1] == {
        "v": 1,
        "type": "game_over",
        "status": "fail",
        "reason": "Time limit reached",
    }
    assert expired == [1]
    assert session.status == "fail"


def test_an_async_on_expire_is_awaited():
    session = a_session()
    events, _ = recorded(session)
    session.started_at -= LIMIT
    expired = []

    async def on_expire():
        expired.append(1)

    asyncio.run(run_timer(session, events, on_expire=on_expire, sleep=Clock(session)))
    assert expired == [1]


def test_returns_immediately_for_a_finished_session():
    session = a_session()
    session.expire()
    events, sent = recorded(session)
    asyncio.run(run_timer(session, events, sleep=Clock(session)))
    assert sent == []


def test_start_timer_cancels_the_previous_emitter():
    """A warm-container reconnect must not leave two 1 Hz emitters running."""
    session = a_session()
    events, _ = recorded(session)

    async def run():
        first = start_timer(session, events)
        second = start_timer(session, events)
        assert first is not second
        await asyncio.sleep(0)
        assert first.cancelled() or first.done()
        assert timer_module._timers["s1"] is second
        cancel_timer("s1")
        await asyncio.sleep(0)
        assert second.cancelled() or second.done()
        assert "s1" not in timer_module._timers

    asyncio.run(run())


def test_cancel_timer_is_a_no_op_without_one():
    cancel_timer("nobody")
