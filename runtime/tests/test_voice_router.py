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
