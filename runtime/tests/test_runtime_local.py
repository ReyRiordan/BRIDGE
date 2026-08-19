"""
The three AWS-only paths BRIDGE_LOCAL=1 switches off inside the SAME runtime
entrypoint: the KVS ICE fetch, the ICE-server build, and the relay-only SDP
filter. Local mode must never fork a second handler, so these assert on
`_handle_offer` itself with the module's collaborators faked out.

`ice_servers=[]` (rather than None) is deliberate and verified against
pipecat-ai 1.3.0: aiortc substitutes its default Google STUN server only when
iceServers is None, so the empty list is what keeps a local run offline.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit import runtime  # noqa: E402
from voice_kit.config import settings  # noqa: E402

HOST_ANSWER = "v=0\r\na=candidate:1 1 udp 2130706431 127.0.0.1 5000 typ host\r\n"


class FakeHandler:
    """Stands in for SmallWebRTCRequestHandler: records ice_servers, answers."""

    last: "FakeHandler | None" = None

    def __init__(self, ice_servers=None, **kwargs):
        self.ice_servers = ice_servers
        FakeHandler.last = self

    async def handle_web_request(self, request, webrtc_connection_callback=None):
        return {"sdp": HOST_ANSWER, "type": "answer"}


@pytest.fixture
def fakes(monkeypatch):
    """Fake out every collaborator `_handle_offer` reaches, and record calls."""
    calls = {"fetch": 0, "build": 0, "filter": 0}

    def fetch_ice_servers(*a, **kw):
        calls["fetch"] += 1
        return [{"urls": ["turn:kvs"], "username": "u", "credential": "c"}]

    def build_ice_servers(servers):
        calls["build"] += 1
        return ["built"]

    def filter_relay_only_sdp(sdp):
        calls["filter"] += 1
        return "filtered"

    monkeypatch.setattr(runtime, "fetch_ice_servers", fetch_ice_servers)
    monkeypatch.setattr(runtime, "build_ice_servers", build_ice_servers)
    monkeypatch.setattr(runtime, "filter_relay_only_sdp", filter_relay_only_sdp)

    # _handle_offer imports pipecat inside the function, so the fake handler is
    # injected through the already-imported module the import resolves against.
    import pipecat.transports.smallwebrtc.request_handler as rh

    monkeypatch.setattr(rh, "SmallWebRTCRequestHandler", FakeHandler)
    return calls


def _offer():
    return {"session_id": "sess-1", "sdp": "offer-sdp", "type": "offer"}


def test_local_mode_skips_kvs_and_the_relay_filter(fakes, monkeypatch):
    monkeypatch.setattr(settings, "bridge_local", True)

    answer = asyncio.run(runtime._handle_offer(_offer()))

    assert fakes == {"fetch": 0, "build": 0, "filter": 0}
    # The host candidate survives — proof the filter never ran.
    assert "typ host" in answer["sdp"]
    assert answer["sdp"] == HOST_ANSWER
    # Empty list, NOT None: None makes aiortc fall back to a public STUN server.
    assert FakeHandler.last.ice_servers == []


def test_deployed_mode_still_fetches_kvs_and_filters(fakes, monkeypatch):
    monkeypatch.setattr(settings, "bridge_local", False)

    answer = asyncio.run(runtime._handle_offer(_offer()))

    assert fakes == {"fetch": 1, "build": 1, "filter": 1}
    assert answer["sdp"] == "filtered"
    assert FakeHandler.last.ice_servers == ["built"]
