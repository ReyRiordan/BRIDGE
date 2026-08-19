"""
The control-plane surface the API Lambda mounts.

Importing voice_kit here doubles as the pipecat-free gate: nothing in this test
installs pipecat, and `create_voice_router()` must still build.
"""

import sys
from pathlib import Path

from fastapi import APIRouter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import voice_kit  # noqa: E402  — needs the sys.path line above


def _routes(router: APIRouter) -> set[tuple[str, frozenset]]:
    return {(route.path, frozenset(route.methods)) for route in router.routes}


def test_create_voice_router_returns_a_router():
    assert isinstance(voice_kit.create_voice_router(), APIRouter)


def test_signaling_endpoints_are_exposed_under_the_prefix():
    assert _routes(voice_kit.create_voice_router()) == {
        ("/voice/{session_id}/start", frozenset({"POST"})),
        ("/voice/{session_id}/signal", frozenset({"POST"})),
        ("/voice/{session_id}/end", frozenset({"POST"})),
    }


def test_prefix_is_configurable():
    paths = {
        path for path, _ in _routes(voice_kit.create_voice_router(prefix="/v1/voice"))
    }

    assert paths == {
        "/v1/voice/{session_id}/start",
        "/v1/voice/{session_id}/signal",
        "/v1/voice/{session_id}/end",
    }


class FakeInvoker(voice_kit.Invoker):
    """Records calls; configurable answer / failure."""

    def __init__(self, *, fail_end: bool = False):
        self.signal_calls: list[dict] = []
        self.end_calls: list[dict] = []
        self.fail_end = fail_end

    async def signal(self, session_id, runtime_session_id, sdp, type="offer"):
        self.signal_calls.append(
            {
                "session_id": session_id,
                "runtime_session_id": runtime_session_id,
                "sdp": sdp,
                "type": type,
            }
        )
        return {"sdp": "answer-sdp", "type": "answer"}

    async def end(self, session_id, runtime_session_id):
        self.end_calls.append(
            {"session_id": session_id, "runtime_session_id": runtime_session_id}
        )
        if self.fail_end:
            raise voice_kit.UpstreamServiceError(message="runtime unreachable")
        return {"status": "ended"}


def _client(invoker):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(voice_kit.create_voice_router(invoker=invoker))
    return TestClient(app)


def test_signal_relays_the_invoker_answer():
    invoker = FakeInvoker()
    resp = _client(invoker).post(
        "/voice/sess-1/signal",
        json={"runtime_session_id": "r" * 33, "sdp": "offer-sdp"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"sdp": "answer-sdp", "type": "answer"}
    assert invoker.signal_calls == [
        {
            "session_id": "sess-1",
            "runtime_session_id": "r" * 33,
            "sdp": "offer-sdp",
            "type": "offer",
        }
    ]


def test_end_with_a_body_invokes_teardown():
    invoker = FakeInvoker()
    resp = _client(invoker).post(
        "/voice/sess-1/end", json={"runtime_session_id": "r" * 33}
    )

    assert resp.status_code == 200
    assert resp.json() == {"message": "Session ended successfully", "transcript": []}
    assert invoker.end_calls == [
        {"session_id": "sess-1", "runtime_session_id": "r" * 33}
    ]


def test_end_without_a_body_skips_teardown():
    invoker = FakeInvoker()
    resp = _client(invoker).post("/voice/sess-1/end")

    assert resp.status_code == 200
    assert invoker.end_calls == []


def test_end_teardown_failure_still_returns_200():
    invoker = FakeInvoker(fail_end=True)
    resp = _client(invoker).post(
        "/voice/sess-1/end", json={"runtime_session_id": "r" * 33}
    )

    assert resp.status_code == 200
    assert resp.json()["message"] == "Session ended successfully"
    assert invoker.end_calls  # it was attempted


def test_start_mints_a_33_plus_char_runtime_session_id(monkeypatch):
    # AgentCore's client-side floor (kit gotcha #5): 33-256 chars.
    from voice_kit.control_plane import router as router_module

    monkeypatch.setattr(router_module, "fetch_ice_servers", lambda: [], raising=True)
    resp = _client(FakeInvoker()).post("/voice/sess-1/start")

    assert resp.status_code == 200
    body = resp.json()
    assert 33 <= len(body["runtime_session_id"]) <= 256
    assert body["session_id"] == "sess-1"
    assert body["ice_servers"] == []


def test_start_degrades_to_empty_ice_servers_when_kvs_is_unreachable(monkeypatch):
    from voice_kit.control_plane import router as router_module

    def boom():
        raise RuntimeError("KVS unreachable")

    monkeypatch.setattr(router_module, "fetch_ice_servers", boom, raising=True)
    resp = _client(FakeInvoker()).post("/voice/sess-1/start")

    assert resp.status_code == 200
    assert resp.json()["ice_servers"] == []


def test_start_in_local_mode_never_touches_kvs(monkeypatch):
    # BRIDGE_LOCAL=1 must skip the fetch outright, not merely tolerate its
    # failure: the zero-AWS promise is that no boto3 call is even attempted.
    from voice_kit.config import settings
    from voice_kit.control_plane import router as router_module

    def boom():
        raise AssertionError("fetch_ice_servers must not be called in local mode")

    monkeypatch.setattr(settings, "bridge_local", True)
    monkeypatch.setattr(router_module, "fetch_ice_servers", boom, raising=True)
    resp = _client(FakeInvoker()).post("/voice/sess-1/start")

    assert resp.status_code == 200
    assert resp.json()["ice_servers"] == []
