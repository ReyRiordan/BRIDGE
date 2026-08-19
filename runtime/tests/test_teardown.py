"""
Post-game teardown: the grace reaper, the session-end hook, and idempotency.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import session as session_module  # noqa: E402
from bridge import timer as timer_module  # noqa: E402
from bridge.config import load_scenario  # noqa: E402
from bridge.emitter import GameEvents  # noqa: E402
from bridge.session import get_or_create_session, get_session  # noqa: E402
from bridge.timer import (  # noqa: E402
    cancel_reaper,
    cancel_timer,
    run_reaper,
    set_pipeline_canceller,
    start_reaper,
    start_timer,
)

SCENARIO = load_scenario()


@pytest.fixture(autouse=True)
def clean_state():
    session_module._sessions.clear()
    timer_module._timers.clear()
    timer_module._reapers.clear()
    yield
    session_module._sessions.clear()
    timer_module._timers.clear()
    timer_module._reapers.clear()
    set_pipeline_canceller(None)


def test_reaper_cancels_the_pipeline_after_the_grace_window():
    ended = []

    async def canceller(session_id):
        ended.append(session_id)

    set_pipeline_canceller(canceller)

    async def run():
        task = start_reaper("s1", delay=0.01)
        await task

    asyncio.run(run())
    assert ended == ["s1"]


def test_reaper_is_armed_only_once():
    set_pipeline_canceller(lambda session_id: asyncio.sleep(0))

    async def run():
        first = start_reaper("s1", delay=5)
        second = start_reaper("s1", delay=5)
        assert first is second
        cancel_reaper("s1")
        await asyncio.sleep(0)
        assert first.cancelled()

    asyncio.run(run())


def test_reaper_without_a_canceller_logs_and_returns():
    set_pipeline_canceller(None)
    asyncio.run(run_reaper("s1", 0, sleep=lambda _s: asyncio.sleep(0)))


def test_cancel_reaper_skips_the_reapers_own_task():
    """The reaper's own path runs end_session -> end hook -> cancel_reaper."""
    ran_to_completion = []

    async def canceller(session_id):
        # This is what the session-end hook does, from inside the reaper task.
        cancel_reaper(session_id)
        ran_to_completion.append(session_id)

    set_pipeline_canceller(canceller)

    async def run():
        await start_reaper("s1", delay=0.01)

    asyncio.run(run())
    assert ran_to_completion == ["s1"]
    assert "s1" not in timer_module._reapers


def test_session_end_hook_clears_everything():
    from bridge.app import on_session_end

    session = get_or_create_session("s1", SCENARIO)
    events = GameEvents("s1", None)

    async def run():
        start_timer(session, events)
        start_reaper("s1", delay=60)
        await on_session_end("s1")
        await asyncio.sleep(0)

    asyncio.run(run())
    assert get_session("s1") is None
    assert "s1" not in timer_module._timers
    assert "s1" not in timer_module._reapers


def test_double_end_is_a_no_op():
    from bridge.app import on_session_end

    get_or_create_session("s1", SCENARIO)

    async def run():
        await on_session_end("s1")
        await on_session_end("s1")

    asyncio.run(run())
    assert get_session("s1") is None
    cancel_timer("s1")
    cancel_reaper("s1")
