"""
TURN URI selection, the ICE-server build, and the relay-only SDP filter.

The load-bearing test here is ``test_the_built_servers_give_aiortc_tls_over_tcp``:
it runs our output through aiortc's own ``connection_kwargs()`` rather than
re-asserting our reading of that function. If aiortc changes its
first-acceptable-URI rule, that test fails instead of production.
"""

import pytest
from voice_kit.kvs import (
    build_ice_servers,
    filter_relay_only_sdp,
    select_turn_uri,
)

HOST = "54-165-221-216.t-99577840.kinesisvideo.us-east-1.amazonaws.com"

# The exact triple KVS returns per server, in the exact order it returns them
# (captured from prod by scripts/cloud_voice_probe.py).
KVS_URIS = [
    f"turn:{HOST}:443?transport=udp",
    f"turns:{HOST}:443?transport=udp",
    f"turns:{HOST}:443?transport=tcp",
]


def _kvs_server(host: str = HOST) -> dict:
    return {
        "urls": [
            f"turn:{host}:443?transport=udp",
            f"turns:{host}:443?transport=udp",
            f"turns:{host}:443?transport=tcp",
        ],
        "username": "user-token",
        "credential": "s3cret",
    }


# --- select_turn_uri -------------------------------------------------------


def test_picks_tls_over_tcp_from_the_real_kvs_triple():
    # Not the first URI in the list — that's the whole point.
    assert select_turn_uri(KVS_URIS) == f"turns:{HOST}:443?transport=tcp"


def test_falls_back_down_the_preference_ladder():
    without_tls = [KVS_URIS[0], f"turn:{HOST}:443?transport=tcp"]
    assert select_turn_uri(without_tls) == f"turn:{HOST}:443?transport=tcp"
    assert select_turn_uri([KVS_URIS[0]]) == KVS_URIS[0]


def test_honors_an_explicit_preference():
    assert select_turn_uri(KVS_URIS, ("turn_udp",)) == KVS_URIS[0]
    # A preference nothing satisfies yields None rather than a silent fallback.
    assert select_turn_uri(KVS_URIS, ("turn_tcp",)) is None


def test_ignores_schemes_and_transports_aiortc_would_reject():
    noise = [
        "stun:stun.example.com:3478",
        "https://example.com",
        f"turns:{HOST}:443?transport=udp",  # aiortc drops turns-over-udp
        f"turn:{HOST}:443?transport=sctp",
        "",
    ]
    assert select_turn_uri(noise) is None


def test_defaults_the_transport_per_rfc_7065_when_the_param_is_absent():
    assert select_turn_uri([f"turns:{HOST}:443"]) == f"turns:{HOST}:443"
    assert select_turn_uri([f"turn:{HOST}:443"], ("turn_udp",)) == f"turn:{HOST}:443"


def test_no_uris_at_all_selects_nothing():
    assert select_turn_uri([]) is None


# --- build_ice_servers -----------------------------------------------------


def test_builds_one_single_uri_server_per_kvs_server():
    servers = build_ice_servers([_kvs_server(), _kvs_server("second.example.com")])

    assert len(servers) == 2
    for server in servers:
        assert isinstance(server.urls, str)
        assert server.urls.startswith("turns:")
        assert server.urls.endswith("?transport=tcp")
        assert server.username == "user-token"
        assert server.credential == "s3cret"


def test_the_transport_setting_selects_the_uri(monkeypatch):
    from voice_kit import kvs

    monkeypatch.setattr(kvs.settings, "turn_transport", "turn_udp")
    assert build_ice_servers([_kvs_server()])[0].urls == KVS_URIS[0]

    monkeypatch.setattr(kvs.settings, "turn_transport", "auto")
    assert build_ice_servers([_kvs_server()])[0].urls == KVS_URIS[2]


def test_a_server_with_nothing_usable_is_dropped_not_raised(monkeypatch):
    from voice_kit import kvs

    monkeypatch.setattr(kvs.settings, "turn_transport", "turn_tcp")
    assert build_ice_servers([_kvs_server()]) == []


def test_no_servers_in_means_no_servers_out():
    assert build_ice_servers([]) == []


def test_the_built_servers_give_aiortc_tls_over_tcp():
    """The regression lock: assert against aiortc's rule, not our reading of it."""
    from aiortc.rtcconfiguration import RTCIceServer
    from aiortc.rtcicetransport import connection_kwargs
    from pipecat.transports.smallwebrtc.connection import IceServer

    # pipecat's IceServer is a bare alias for aiortc's RTCIceServer, so what
    # build_ice_servers returns is literally what aiortc reads. Pin that too —
    # if pipecat starts wrapping instead, this test must be revisited.
    assert IceServer is RTCIceServer

    servers = build_ice_servers([_kvs_server(), _kvs_server("second.example.com")])
    kwargs = connection_kwargs(servers)

    # One TURN server for the whole connection, and it is the FIRST input
    # server's TLS/TCP URI — the second is ignored, by aiortc's own rule.
    assert kwargs["turn_server"] == (HOST, 443)
    assert kwargs["turn_transport"] == "tcp"
    assert kwargs["turn_ssl"] is True
    assert kwargs["turn_username"] == "user-token"
    assert kwargs["turn_password"] == "s3cret"


def test_the_unselected_kvs_list_would_have_given_aiortc_udp():
    """Why the selection exists: raw KVS order lands aiortc on UDP TURN."""
    from aiortc import RTCIceServer
    from aiortc.rtcicetransport import connection_kwargs

    kwargs = connection_kwargs(
        [RTCIceServer(urls=KVS_URIS, username="user-token", credential="s3cret")]
    )
    assert kwargs["turn_transport"] == "udp"
    assert kwargs["turn_ssl"] is False


# --- filter_relay_only_sdp -------------------------------------------------

RELAY = "a=candidate:1 1 udp 41885439 3.4.5.6 50000 typ relay raddr 1.2.3.4 rport 5000"
HOST_CAND = "a=candidate:2 1 udp 2130706431 10.0.0.5 40000 typ host"
SRFLX = (
    "a=candidate:3 1 udp 1694498815 1.2.3.4 40001 typ srflx raddr 10.0.0.5 rport 40000"
)


def test_only_relay_candidates_survive():
    sdp = "\r\n".join(
        ["v=0", "m=audio 9 UDP/TLS/RTP/SAVPF 111", HOST_CAND, RELAY, SRFLX]
    )
    kept = filter_relay_only_sdp(sdp).splitlines()

    assert RELAY in kept
    assert HOST_CAND not in kept
    assert SRFLX not in kept
    assert "v=0" in kept  # non-candidate lines pass through untouched


def test_a_host_only_answer_is_stripped_to_zero_candidates():
    # This is the outage shape: gathering produced no relay, so the browser
    # receives an answer with nothing to check against and stalls.
    sdp = "\r\n".join(["v=0", "m=audio 9 UDP/TLS/RTP/SAVPF 111", HOST_CAND])
    kept = filter_relay_only_sdp(sdp).splitlines()

    assert not [line for line in kept if "candidate:" in line]
    assert kept == ["v=0", "m=audio 9 UDP/TLS/RTP/SAVPF 111"]


def test_a_trailing_newline_is_preserved():
    assert filter_relay_only_sdp("v=0\r\n").endswith("\r\n")
    assert not filter_relay_only_sdp("v=0").endswith("\r\n")


@pytest.mark.parametrize("prefix", ["a=candidate:", "candidate:"])
def test_both_candidate_line_spellings_are_filtered(prefix):
    line = prefix + "2 1 udp 2130706431 10.0.0.5 40000 typ host"
    assert filter_relay_only_sdp("v=0\r\n" + line) == "v=0"


# --- the candidate histogram on the answer path ---------------------------


def test_the_histogram_warns_only_when_no_relay_survives(caplog):
    from voice_kit.runtime import _log_candidate_types

    with caplog.at_level("INFO", logger="voice_kit.runtime"):
        _log_candidate_types("\r\n".join(["v=0", HOST_CAND, SRFLX]), "sess-1")
    assert "host=1, srflx=1" in caplog.text
    assert "ZERO relay candidates" in caplog.text

    caplog.clear()
    with caplog.at_level("INFO", logger="voice_kit.runtime"):
        _log_candidate_types("\r\n".join(["v=0", HOST_CAND, RELAY]), "sess-2")
    assert "host=1, relay=1" in caplog.text
    assert "ZERO relay" not in caplog.text


def test_the_histogram_survives_a_candidate_free_answer(caplog):
    from voice_kit.runtime import _log_candidate_types

    with caplog.at_level("INFO", logger="voice_kit.runtime"):
        _log_candidate_types("v=0\r\n", "sess-3")
    assert "gathered candidates: none" in caplog.text
    assert "ZERO relay candidates" in caplog.text
