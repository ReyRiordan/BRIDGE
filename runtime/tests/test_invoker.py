"""
The Invoker interface: AgentCoreInvoker's boto3 invoke shape + error surfacing,
LocalInvoker's HTTP shape + its identical error contract, and get_invoker()'s
env dispatch.

LocalInvoker defers its aiohttp import, so the tests swap a fake into
sys.modules (the same trick the boto3 fake plays for AgentCoreInvoker) — no
network, no aiohttp behaviour under test.
"""

import asyncio
import io
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit import (  # noqa: E402
    AgentCoreInvoker,
    Invoker,
    LocalInvoker,
    UpstreamServiceError,
    get_invoker,
)
from voice_kit.config import settings  # noqa: E402
from voice_kit.control_plane import agentcore, local  # noqa: E402


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


def test_get_invoker_defaults_to_agentcore(monkeypatch):
    # Explicit: a developer's repo-root .env now reaches VoiceKitSettings, so
    # the "default" must be pinned rather than assumed.
    monkeypatch.setattr(settings, "voice_invoker", "agentcore")

    invoker = get_invoker()

    assert isinstance(invoker, AgentCoreInvoker)
    assert isinstance(invoker, Invoker)


def test_get_invoker_local_builds_the_local_invoker(monkeypatch):
    monkeypatch.setattr(settings, "voice_invoker", "local")

    invoker = get_invoker()

    assert isinstance(invoker, LocalInvoker)
    assert isinstance(invoker, Invoker)


# --------------------------------------------------------------------------
# LocalInvoker
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeClientSession:
    """Records the one POST the invoker makes; raises on demand."""

    last: "FakeClientSession | None" = None

    def __init__(self, response=None, error=None, timeout=None):
        self._response = response
        self._error = error
        self.timeout = timeout
        self.calls: list[dict] = []
        FakeClientSession.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture
def aiohttp_fake(monkeypatch):
    """Install a fake `aiohttp` module the deferred import resolves to."""

    def install(*, status: int = 200, body: str = "{}", error: Exception | None = None):
        module = types.ModuleType("aiohttp")
        module.ClientTimeout = lambda total=None: {"total": total}
        module.ClientSession = lambda timeout=None: FakeClientSession(
            response=FakeResponse(status, body), error=error, timeout=timeout
        )
        monkeypatch.setitem(sys.modules, "aiohttp", module)
        return module

    return install


def test_local_signal_posts_the_offer_payload_and_returns_the_answer(aiohttp_fake):
    aiohttp_fake(body=json.dumps({"sdp": "answer-sdp", "type": "answer"}))

    answer = _run(
        LocalInvoker(base_url="http://localhost:8080").signal(
            "sess-1", "voicekit-" + "a" * 32, sdp="offer-sdp"
        )
    )

    assert answer == {"sdp": "answer-sdp", "type": "answer"}
    call = FakeClientSession.last.calls[0]
    assert call["url"] == "http://localhost:8080/invocations"
    # No "action" key — byte-for-byte the AgentCore signal payload.
    assert call["json"] == {
        "session_id": "sess-1",
        "sdp": "offer-sdp",
        "type": "offer",
    }
    # Envelope parity with invoke_agent_runtime(runtimeSessionId=...).
    assert (
        call["headers"]["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"]
        == "voicekit-" + "a" * 32
    )


def test_local_base_url_defaults_to_settings(aiohttp_fake, monkeypatch):
    monkeypatch.setattr(settings, "voice_runtime_url", "http://127.0.0.1:9999/")
    aiohttp_fake(body="{}")

    _run(LocalInvoker().signal("s", "r" * 33, sdp="o"))

    assert FakeClientSession.last.calls[0]["url"] == "http://127.0.0.1:9999/invocations"


def test_local_end_posts_the_end_action(aiohttp_fake):
    aiohttp_fake(body=json.dumps({"status": "ok"}))

    result = _run(LocalInvoker(base_url="http://rt").end("sess-1", "r" * 33))

    assert result == {"status": "ok"}
    assert FakeClientSession.last.calls[0]["json"] == {
        "session_id": "sess-1",
        "action": "end",
    }


def test_local_timeout_exceeds_the_runtime_offer_timeout():
    # The runtime bounds negotiation at OFFER_TIMEOUT_SECONDS = 30 and answers
    # its own stall in-band; a client timeout at or below that would replace
    # that precise message with a generic transport failure. Asserted against
    # the literal rather than importing voice_kit.runtime, which pulls pipecat.
    assert local.REQUEST_TIMEOUT_SECONDS > 30


def test_local_in_band_error_surfaces_as_upstream_service_error(aiohttp_fake):
    aiohttp_fake(body=json.dumps({"error": "offer handling timed out"}))

    with pytest.raises(UpstreamServiceError) as exc:
        _run(LocalInvoker(base_url="http://rt").signal("s", "r" * 33, sdp="o"))

    assert exc.value.details == {"error": "offer handling timed out"}
    assert exc.value.status_code == 502


def test_local_http_error_carries_a_retriable_upstream_status(aiohttp_fake):
    aiohttp_fake(status=500, body="boom")

    with pytest.raises(UpstreamServiceError) as exc:
        _run(LocalInvoker(base_url="http://rt").signal("s", "r" * 33, sdp="o"))

    assert exc.value.upstream_status == 500
    assert exc.value.is_retriable


def test_local_transport_failure_never_leaks_a_bare_exception(aiohttp_fake):
    aiohttp_fake(error=OSError("connection refused"))

    with pytest.raises(UpstreamServiceError) as exc:
        _run(LocalInvoker(base_url="http://rt").signal("s", "r" * 33, sdp="o"))

    assert "could not be reached" in exc.value.message
    assert "connection refused" in exc.value.details["error"]


def test_local_malformed_json_surfaces_as_upstream_service_error(aiohttp_fake):
    aiohttp_fake(body="<html>not json</html>")

    with pytest.raises(UpstreamServiceError) as exc:
        _run(LocalInvoker(base_url="http://rt").signal("s", "r" * 33, sdp="o"))

    assert "malformed" in exc.value.message
