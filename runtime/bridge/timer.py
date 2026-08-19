"""
The session clock and the post-game grace reaper.

Two per-session asyncio tasks, both registered in module-level dicts (the same
pattern as ``voice_kit.runtime._pipeline_tasks``) and both **pop-based and
idempotent**, so a double teardown is a no-op rather than an error:

- **timer** — a 1 Hz emitter. Elapsed time is derived from the session's
  monotonic ``started_at``, never accumulated in the loop, so a warm-container
  reconnect resumes a continuous clock even though the task is new.
- **reaper** — started when the game ends, it gives the client a grace window to
  render the debrief and then cancels the pipeline through the registered
  canceller (``voice_kit.runtime.end_session``, wired in ``app.py``).

Teardown interplay:

| Trigger | Effect |
|---|---|
| Referee terminal | ``game_over`` -> reaper armed; the timer stops on its next tick |
| Timer expiry | ``game_over{fail}`` -> ``on_expire`` -> reaper armed; the timer returns |
| Reaper fires | ``end_session`` -> pipeline cancelled -> end hook -> timer + reaper + session dropped |
| ``"end"`` action | the same ``end_session`` path, immediately |
"""

import asyncio
import inspect
import logging
from typing import Awaitable, Callable, Dict, Optional

from .config import GAME_GRACE_SECONDS

logger = logging.getLogger(__name__)

# session_id -> task. In-process, single loop, no locks.
_timers: Dict[str, asyncio.Task] = {}
_reapers: Dict[str, asyncio.Task] = {}

# Set by app.py to voice_kit.runtime.end_session. Kept injectable so the timer
# module does not import the runtime (and therefore pipecat) itself.
_pipeline_canceller: Optional[Callable[[str], Awaitable]] = None


def set_pipeline_canceller(fn: Optional[Callable[[str], Awaitable]]) -> None:
    global _pipeline_canceller
    _pipeline_canceller = fn


async def _maybe_await(result) -> None:
    if inspect.isawaitable(result):
        await result


# --- timer ----------------------------------------------------------------


async def run_timer(session, events, on_expire=None, *, sleep=asyncio.sleep) -> None:
    """Emit a ``timer`` event once a second until the session ends or expires.

    ``sleep`` is injectable so tests run instantly.
    """
    limit = session.time_limit
    while True:
        if session.status != "active":
            # Terminal via the referee, or the session was torn down.
            return
        elapsed = session.elapsed_seconds()
        events.timer(min(elapsed, limit), limit)
        if elapsed >= limit:
            events.game_over(*session.expire())
            if on_expire is not None:
                await _maybe_await(on_expire())
            return
        await sleep(1)


def start_timer(session, events, on_expire=None) -> asyncio.Task:
    """(Re)start this session's clock, cancelling any previous one first.

    A warm-container reconnect rebuilds the pipeline and would otherwise leave
    two 1 Hz emitters on the same session.
    """
    cancel_timer(session.session_id)
    task = asyncio.create_task(run_timer(session, events, on_expire=on_expire))
    _timers[session.session_id] = task
    return task


def cancel_timer(session_id: str) -> None:
    """Stop this session's clock. No-op when none is running."""
    task = _timers.pop(session_id, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()


# --- reaper ---------------------------------------------------------------


async def run_reaper(session_id: str, delay: float, *, sleep=asyncio.sleep) -> None:
    """After the grace window, cancel the session's pipeline."""
    await sleep(delay)
    if _pipeline_canceller is None:
        logger.warning("[%s] no pipeline canceller registered", session_id)
        return
    logger.info("[%s] grace window elapsed; ending session", session_id)
    await _pipeline_canceller(session_id)


def start_reaper(session_id: str, delay: float = GAME_GRACE_SECONDS) -> asyncio.Task:
    """Arm the grace reaper once. A second call while one is pending is a no-op."""
    existing = _reapers.get(session_id)
    if existing is not None:
        return existing
    task = asyncio.create_task(run_reaper(session_id, delay))
    _reapers[session_id] = task
    return task


def cancel_reaper(session_id: str) -> None:
    """Disarm the grace reaper. No-op when none is pending.

    The reaper's own path runs ``end_session`` -> end hook -> here, so cancelling
    the task when it IS the running task would kill the teardown mid-flight (the
    same guard ``voice_kit.runtime._run_task`` applies to superseded pipelines).
    """
    task = _reapers.pop(session_id, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()
