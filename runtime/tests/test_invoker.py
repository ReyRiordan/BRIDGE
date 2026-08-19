"""
The Invoker interface: AgentCoreInvoker's boto3 invoke shape + error
surfacing, and get_invoker()'s env dispatch.
"""

import asyncio
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit import (  # noqa: E402
    AgentCoreInvoker,
    Invoker,
    UpstreamServiceError,
    get_invoker,
)
from voice_kit.config import settings  # noqa: E402
from voice_kit.control_plane import agentcore  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    """A fake bedrock-agentcore client wired into the lazy module client."""
    fake = MagicMock()
    monkeypatch.setattr(agentcore, "_agentcore_client", fake)
    return fake


def _respond_with(client, payload: dict) -> None:
    client.invoke_agent_runtime.return_value = {
        "response": io.BytesIO(json.dumps(payload).encode())
    }


def _run(coro):
    return asyncio.run(coro)


def test_signal_sends_the_offer_payload_and_returns_the_answer(client):
    _respond_with(client, {"sdp": "answer-sdp", "type": "answer"})
    invoker = AgentCoreInvoker(runtime_arn="arn:aws:test:runtime")

    answer = _run(invoker.signal("sess-1", "voicekit-" + "a" * 32, sdp="offer-sdp"))

    assert answer == {"sdp": "answer-sdp", "type": "answer"}
    kwargs = client.invoke_agent_runtime.call_args.kwargs
    assert kwargs["agentRuntimeArn"] == "arn:aws:test:runtime"
    assert kwargs["runtimeSessionId"] == "voicekit-" + "a" * 32
    # No "action" key: the runtime's dispatch default is "signal".
    assert json.loads(kwargs["payload"]) == {
        "session_id": "sess-1",
        "sdp": "offer-sdp",
        "type": "offer",
    }


def test_runtime_arn_defaults_to_settings(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_runtime_arn", "arn:aws:from-settings")
    _respond_with(client, {"sdp": "x", "type": "answer"})

    _run(AgentCoreInvoker().signal("s", "r" * 33, sdp="o"))

    kwargs = client.invoke_agent_runtime.call_args.kwargs
    assert kwargs["agentRuntimeArn"] == "arn:aws:from-settings"


def test_end_sends_the_end_action(client):
    _respond_with(client, {"status": "ended"})

    result = _run(AgentCoreInvoker(runtime_arn="arn").end("sess-1", "r" * 33))

    assert result == {"status": "ended"}
    kwargs = client.invoke_agent_runtime.call_args.kwargs
    assert json.loads(kwargs["payload"]) == {"session_id": "sess-1", "action": "end"}


def test_botocore_errors_surface_as_upstream_service_error(client):
    import botocore.exceptions

    client.invoke_agent_runtime.side_effect = botocore.exceptions.BotoCoreError()

    with pytest.raises(UpstreamServiceError):
        _run(AgentCoreInvoker(runtime_arn="arn").signal("s", "r" * 33, sdp="o"))


def test_error_bodies_surface_as_upstream_service_error(client):
    _respond_with(client, {"error": "offer handling timed out"})

    with pytest.raises(UpstreamServiceError) as exc:
        _run(AgentCoreInvoker(runtime_arn="arn").signal("s", "r" * 33, sdp="o"))

    assert exc.value.details == {"error": "offer handling timed out"}


def test_get_invoker_defaults_to_agentcore():
    invoker = get_invoker()
    assert isinstance(invoker, AgentCoreInvoker)
    assert isinstance(invoker, Invoker)


def test_get_invoker_local_is_reserved_for_rewrite_h(monkeypatch):
    monkeypatch.setattr(settings, "voice_invoker", "local")
    with pytest.raises(NotImplementedError):
        get_invoker()
