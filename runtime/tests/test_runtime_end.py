"""
The entrypoint's action dispatch and the end_session teardown: cancel the
pipeline task, run the host's end hook, stay idempotent.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit import runtime as runtime_module  # noqa: E402
from voice_kit import set_session_end_hook  # noqa: E402


@pytest.fixture(autouse=True)
def clean_runtime():
    runtime_module._pipeline_tasks.clear()
    yield
    runtime_module._pipeline_tasks.clear()
    set_session_end_hook(None)


def test_end_action_never_touches_sdp(monkeypatch):
    """The end payload carries no sdp — dispatch must precede any sdp access."""
    calls = []

    async def fake_end_session(session_id):
        calls.append(session_id)
        return {"status": "ok", "session_id": session_id}

    monkeypatch.setattr(runtime_module, "end_session", fake_end_session)

    result = runtime_module.handle_offer({"session_id": "sess-1", "action": "end"})

    assert result == {"status": "ok", "session_id": "sess-1"}
    assert calls == ["sess-1"]


def test_missing_action_takes_the_signal_path(monkeypatch):
    async def fake_handle_offer(payload):
        return {"sdp": "answer", "type": "answer"}

    monkeypatch.setattr(runtime_module, "_handle_offer", fake_handle_offer)

    result = runtime_module.handle_offer({"session_id": "sess-1", "sdp": "offer"})

    assert result == {"sdp": "answer", "type": "answer"}


def test_unknown_action_returns_an_error():
    result = runtime_module.handle_offer({"session_id": "sess-1", "action": "nope"})

    assert result == {"error": "unknown action: nope"}


def test_end_session_cancels_the_task_and_runs_the_hook():
    ended = []
    set_session_end_hook(lambda session_id: _record(ended, session_id))

    async def scenario():
        async def forever():
            await asyncio.Event().wait()

        task = asyncio.get_running_loop().create_task(forever())
        runtime_module._pipeline_tasks["sess-1"] = task
        await asyncio.sleep(0)

        result = await runtime_module.end_session("sess-1")

        assert task.cancelled()
        assert result == {"status": "ok", "session_id": "sess-1"}
        assert "sess-1" not in runtime_module._pipeline_tasks

    asyncio.run(scenario())
    assert ended == ["sess-1"]


def test_double_end_is_a_no_op():
    ended = []
    set_session_end_hook(lambda session_id: _record(ended, session_id))

    async def scenario():
        await runtime_module.end_session("sess-1")
        return await runtime_module.end_session("sess-1")

    result = asyncio.run(scenario())

    # No task to cancel either time; still ok, hook still safe to re-run.
    assert result == {"status": "ok", "session_id": "sess-1"}
    assert ended == ["sess-1", "sess-1"]


def test_end_session_swallows_a_failing_hook():
    async def boom(session_id):
        raise RuntimeError("host teardown blew up")

    set_session_end_hook(boom)

    assert asyncio.run(runtime_module.end_session("sess-1")) == {
        "status": "ok",
        "session_id": "sess-1",
    }


async def _record(sink: list, session_id: str) -> None:
    sink.append(session_id)
