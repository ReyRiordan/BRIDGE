"""
The session recorder: turns game events into session-record updates, off the
hot path.

Every public method is **synchronous and non-blocking**. It builds one update
dict (see ``store.py`` for the shape) and ``put_nowait``s it on a bounded
per-session queue, which costs microseconds. A writer task, started lazily
on the running loop, drains the queue serially (so the order is preserved)
and runs the blocking table call in ``asyncio.to_thread``. The loop never
waits on DynamoDB.

The recorder **fails open**, like the referee. A failed or slow write is
logged and dropped. A full queue drops the update with a warning. Nothing
here can stop or delay the conversation.

Callers record after the turn's events have been emitted, so the browser
never waits on the recorder either.

``t`` on every turn and timeline point is seconds since game start (the
on-screen clock); ``at`` is ISO wall-clock UTC.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

from voice_kit.types import TranscriptMessage

from .store import SessionTable

logger = logging.getLogger(__name__)

DEFAULT_MAXSIZE = 256


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionRecorder:
    def __init__(self, session_id: str, table: SessionTable, maxsize=DEFAULT_MAXSIZE):
        self.session_id = session_id
        self._table = table
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._writer: Optional[asyncio.Task] = None
        self._closed = False
        # One `started` per GameSession: a reconnect must not reset the record.
        self.has_started = False
        self._ended = False
        self._last_escalation: Optional[int] = None

    # --- events -----------------------------------------------------------

    def started(self, session) -> None:
        if self.has_started:
            return
        self.has_started = True
        self._last_escalation = session.escalation
        self._enqueue(
            {
                "set": {
                    "status": "active",
                    "started_at": _now_iso(),
                    "escalation": session.escalation,
                    "max_escalation": session.max_escalation,
                    "goal": session.goal,
                    "time_limit_s": session.time_limit,
                    "updated_at": _now_iso(),
                },
                "append": {
                    "timeline": [
                        {"t": 0, "escalation": session.escalation, "cause": "start"}
                    ]
                },
            }
        )

    def student_turn(
        self, session, message: TranscriptMessage, applied: List[dict]
    ) -> None:
        t = self._t(session)
        actions = [
            {
                "type": a["type"],
                "point_change": a["point_change"],
                "repeat": bool(a.get("repeat")),
            }
            for a in applied
        ]
        update = {
            "set": {"escalation": session.escalation, "updated_at": _now_iso()},
            "append": {
                "turns": [
                    {
                        "role": "student",
                        "t": t,
                        "at": _now_iso(),
                        "content": message.content,
                        "source": message.source,
                        "actions": actions,
                        "escalation_after": session.escalation,
                    }
                ],
                "timeline": self._timeline_point(session, t, "turn"),
            },
            "add": {},
        }
        helpful = sum(1 for a in actions if a["point_change"] < 0)
        escalating = sum(1 for a in actions if a["point_change"] > 0)
        if helpful:
            update["add"]["helpful_count"] = helpful
        if escalating:
            update["add"]["escalating_count"] = escalating
        self._enqueue(update)

    def patient_turn(self, session, message: TranscriptMessage) -> None:
        self._enqueue(
            {
                "set": {"updated_at": _now_iso()},
                "append": {
                    "turns": [
                        {
                            "role": "patient",
                            "t": self._t(session),
                            "at": _now_iso(),
                            "content": message.content,
                        }
                    ]
                },
            }
        )

    def inactivity(self, session) -> None:
        t = self._t(session)
        self._enqueue(
            {
                "set": {"escalation": session.escalation, "updated_at": _now_iso()},
                "append": {"timeline": self._timeline_point(session, t, "inactivity")},
            }
        )

    def ended(self, session) -> None:
        if self._ended:
            return
        self._ended = True
        self._end_update(session, session.status, session.end_reason, session.reason)

    def abandoned(self, session) -> None:
        if self._ended:
            return
        self._ended = True
        self._end_update(session, "abandoned", "abandoned", None)

    async def close(self, timeout: float = 2.0) -> None:
        """Drain what is queued (bounded by ``timeout``), then stop the writer."""
        self._closed = True
        self._ensure_writer()
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "[%s] session record: %d update(s) not written before close",
                self.session_id,
                self._queue.qsize(),
            )
        if self._writer is not None:
            self._writer.cancel()
            self._writer = None

    # --- internals --------------------------------------------------------

    def _end_update(self, session, status, end_reason, reason) -> None:
        ended_at = session.ended_at
        duration = (ended_at if ended_at is not None else time.monotonic()) - (
            session.started_at
        )
        self._enqueue(
            {
                "set": {
                    "status": status,
                    "end_reason": end_reason,
                    "reason": reason,
                    "escalation": session.escalation,
                    "ended_at": _now_iso(),
                    "duration_s": round(duration, 1),
                    "updated_at": _now_iso(),
                }
            }
        )

    def _t(self, session) -> float:
        return round(session.elapsed(), 1)

    def _timeline_point(self, session, t: float, cause: str) -> list:
        """A timeline point when the escalation changed since the last one."""
        if session.escalation == self._last_escalation:
            return []
        self._last_escalation = session.escalation
        return [{"t": t, "escalation": session.escalation, "cause": cause}]

    def _enqueue(self, update: dict) -> None:
        if self._closed:
            logger.warning(
                "[%s] session record closed; update dropped", self.session_id
            )
            return
        try:
            self._queue.put_nowait(update)
        except asyncio.QueueFull:
            logger.warning(
                "[%s] session record queue full; update dropped", self.session_id
            )
            return
        self._ensure_writer()

    def _ensure_writer(self) -> None:
        if self._writer is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # started by the next call made on a loop
        self._writer = loop.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            update = await self._queue.get()
            try:
                await asyncio.to_thread(self._table.update, self.session_id, update)
            except Exception:
                logger.warning(
                    "[%s] session record write failed; update dropped",
                    self.session_id,
                    exc_info=True,
                )
            finally:
                self._queue.task_done()
