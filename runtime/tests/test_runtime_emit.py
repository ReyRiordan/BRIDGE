"""
The emit seam: what actually reaches `connection.send_app_message`.

The seam carries a JSON string, but pipecat serializes whatever it is handed.
Passing the string through double-encodes every game event — the browser's
`JSON.parse` then yields a string instead of an event object and the reducer
drops it, so the transcript and timer never move while audio works fine. This
pins the decode down at the one boundary where the two conventions meet.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit import runtime  # noqa: E402
from voice_kit.config import settings  # noqa: E402


class FakeConnection:
    """Records exactly what pipecat would be asked to serialize."""

    def __init__(self):
        self.messages = []

    def send_app_message(self, message):
        self.messages.append(message)


class ConnectingHandler:
    """A request handler that runs the connection callback, as pipecat's does."""

    connection: "FakeConnection | None" = None

    def __init__(self, ice_servers=None, **kwargs):
        pass

    async def handle_web_request(self, request, webrtc_connection_callback=None):
        ConnectingHandler.connection = FakeConnection()
        await webrtc_connection_callback(ConnectingHandler.connection)
        return {"sdp": "answer-sdp", "type": "answer"}


class FakeContext:
    task = object()


@pytest.fixture
def emitter(monkeypatch):
    """Run `_handle_offer` far enough to capture the emit callback it builds."""
    captured = {}

    async def build_pipeline_for_session(session_id, transport, emit=None):
        captured["emit"] = emit
        return FakeContext()

    class FakeLoop:
        def create_task(self, coro):
            coro.close()

    monkeypatch.setattr(settings, "bridge_local", True)
    monkeypatch.setattr(
        runtime, "build_pipeline_for_session", build_pipeline_for_session
    )
    monkeypatch.setattr(runtime, "_loop", FakeLoop())

    import pipecat.transports.smallwebrtc.request_handler as rh
    import pipecat.transports.smallwebrtc.transport as tr

    monkeypatch.setattr(rh, "SmallWebRTCRequestHandler", ConnectingHandler)
    monkeypatch.setattr(tr, "SmallWebRTCTransport", lambda **kwargs: object())

    asyncio.run(
        runtime._handle_offer(
            {"session_id": "sess-1", "sdp": "offer-sdp", "type": "offer"}
        )
    )
    return captured["emit"], ConnectingHandler.connection


def test_emit_hands_pipecat_the_object_not_the_json_string(emitter):
    emit, connection = emitter

    emit(json.dumps({"v": 1, "type": "timer", "elapsed": 3, "limit": 300}))

    # A str here would reach the browser as a quoted string, not an event.
    assert connection.messages == [
        {"v": 1, "type": "timer", "elapsed": 3, "limit": 300}
    ]
