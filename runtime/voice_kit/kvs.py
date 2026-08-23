"""
Kinesis Video Streams ICE/TURN helpers for the voice runtime.

The voice runtime never ships STUN/TURN servers to the browser. Instead it asks
KVS for managed TURN credentials (``GetIceServerConfig``), configures the aiortc
peer connection to use **relay only**, and strips every non-relay candidate from
the SDP answer before returning it. The browser therefore connects exclusively
over KVS-managed TURN, and internal host candidates (which the browser could
never reach) never leak into signaling.

The runtime's own TURN leg runs over TLS/TCP by default (``TURN_TRANSPORT``):
aiortc keeps exactly one TURN URI per connection, and the UDP allocation does
not complete from AgentCore's managed PUBLIC network. The relay candidate aioice
emits is an ordinary UDP relay candidate either way, so the browser side and
:func:`filter_relay_only_sdp` are unaffected.

Operational lessons honored here (from the AWS "Pipecat voice agents on
AgentCore" blog):

- **Lazy KVS init.** ``GetIceServerConfig`` is called per session, NOT at import
  or container start: IAM creds are not yet available when the container boots,
  so an import-time call crashes the runtime. All boto3 clients are created
  inside functions for the same reason.
- **Relay-only via SDP filter.** The TURN servers are passed to
  ``SmallWebRTCConnection`` and we scrub the answer SDP of host/srflx/prflx
  candidates so only ``typ relay`` lines survive (aiortc has no relay-only
  transport-policy knob, so the SDP filter is the enforcement point).

NOTE: The exact KVS signaling API shape (endpoint discovery → GetIceServerConfig)
and aiortc's relay-only configuration knob postdate the assistant's knowledge
cutoff; the call sites below are marked for deploy-time verification.
"""

import logging
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .config import settings

logger = logging.getLogger(__name__)

# Preference order used when ``turn_transport`` is "auto". TLS-over-TCP first:
# aiortc keeps exactly ONE TURN URI per connection and on AgentCore's managed
# PUBLIC network the UDP allocation never completes (gotcha #14).
TURN_URI_PREFERENCE: Tuple[str, ...] = ("turns_tcp", "turn_tcp", "turn_udp")


def _classify_turn_uri(uri: str) -> Optional[str]:
    """Map one TURN URI to a ``turns_tcp`` / ``turn_tcp`` / ``turn_udp`` kind.

    Returns ``None`` for anything else (``stun:``, a bogus scheme, a transport
    aiortc would reject). TURN URIs are not standard URLs — the host lives in
    the path and the transport rides a ``?transport=`` query — so the scheme is
    split off by hand rather than trusting ``urlparse`` netloc parsing.
    """
    scheme, _, rest = uri.partition(":")
    scheme = scheme.strip().lower()
    if scheme not in ("turn", "turns"):
        return None

    query = urlparse("//" + rest).query if "?" in rest else ""
    transport = ""
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key.strip().lower() == "transport":
            transport = value.strip().lower()

    # RFC 7065: no transport param means UDP for turn:, TCP for turns:.
    if not transport:
        transport = "tcp" if scheme == "turns" else "udp"
    if transport not in ("udp", "tcp"):
        return None
    # aiortc refuses turns: over UDP outright (rtcicetransport.connection_kwargs).
    if scheme == "turns" and transport != "tcp":
        return None
    return f"{scheme}_{transport}"


def select_turn_uri(
    uris: Sequence[str], preference: Sequence[str] = TURN_URI_PREFERENCE
) -> Optional[str]:
    """Pick the one TURN URI aiortc should use for a connection.

    aiortc's ``connection_kwargs()`` keeps the FIRST acceptable TURN URI it sees
    and ignores every later one, across all servers. KVS hands out
    ``[turn:...?transport=udp, turns:...?transport=udp, turns:...?transport=tcp]``
    in that order, so inheriting list order means UDP always wins — and UDP TURN
    does not complete on the managed PUBLIC network. Choosing here makes the
    transport an explicit decision instead of an accident of ordering.

    Args:
        uris: TURN/STUN URIs for a single KVS server.
        preference: Kinds to try, best first. Defaults to
            :data:`TURN_URI_PREFERENCE`.

    Returns:
        The first URI whose kind matches the earliest satisfiable preference, or
        ``None`` if nothing in ``uris`` is usable.
    """
    classified = [(uri, _classify_turn_uri(uri)) for uri in uris]
    for kind in preference:
        for uri, uri_kind in classified:
            if uri_kind == kind:
                return uri
    return None


def fetch_ice_servers(channel_name: str | None = None) -> List[dict]:
    """
    Lazily fetch KVS-managed TURN credentials for one session.

    Discovers the signaling channel's HTTPS endpoint, then calls
    ``GetIceServerConfig`` to obtain short-lived TURN URIs + credentials. Called
    once per peer connection — never at import (IAM creds unavailable at boot).

    Args:
        channel_name: KVS signaling channel name; defaults to
            ``settings.kvs_channel_name``.

    Returns:
        A list of ICE server dicts (``{"urls", "username", "credential"}``)
        suitable for building an aiortc ``RTCConfiguration``.

    Raises:
        ValueError: If no channel name is configured.
    """
    # boto3 is imported lazily so this module can be imported (e.g. in tests)
    # without the voice dependencies installed, and so no client is built at
    # container start before IAM creds are ready.
    import boto3

    channel_name = channel_name or settings.kvs_channel_name
    if not channel_name:
        raise ValueError("kvs_channel_name is not configured")

    region = settings.aws_region
    kvs = boto3.client("kinesisvideo", region_name=region)

    # Resolve the channel ARN, then its HTTPS signaling endpoint.
    # NOTE: verify endpoint protocol/role names at deploy time.
    describe = kvs.describe_signaling_channel(ChannelName=channel_name)
    channel_arn = describe["ChannelInfo"]["ChannelARN"]

    endpoints = kvs.get_signaling_channel_endpoint(
        ChannelARN=channel_arn,
        SingleMasterChannelEndpointConfiguration={
            "Protocols": ["HTTPS"],
            "Role": "MASTER",
        },
    )
    https_endpoint = next(
        e["ResourceEndpoint"]
        for e in endpoints["ResourceEndpointList"]
        if e["Protocol"] == "HTTPS"
    )

    signaling = boto3.client(
        "kinesis-video-signaling",
        endpoint_url=https_endpoint,
        region_name=region,
    )
    config = signaling.get_ice_server_config(ChannelARN=channel_arn)

    ice_servers: List[dict] = []
    for server in config.get("IceServerList", []):
        ice_servers.append(
            {
                "urls": server["Uris"],
                "username": server["Username"],
                "credential": server["Password"],
            }
        )

    logger.info(
        "Fetched %d KVS ICE server(s) for channel %s",
        len(ice_servers),
        channel_name,
    )
    return ice_servers


def build_ice_servers(ice_servers: List[dict]):
    """
    Convert KVS ICE dicts into Pipecat ``IceServer`` objects for SmallWebRTC.

    ``SmallWebRTCConnection(ice_servers=[...])`` owns the aiortc peer connection
    and its SDP negotiation, so we hand it Pipecat ``IceServer``s (which it
    normalizes to ``aiortc.RTCIceServer`` internally) rather than building an
    ``RTCConfiguration`` ourselves. Relay-only behavior is enforced downstream by
    :func:`filter_relay_only_sdp`, which strips every non-relay candidate from the
    answer SDP before it is returned to the browser.

    Each server contributes a **single** URI, chosen by :func:`select_turn_uri`
    under ``settings.turn_transport``, rather than the raw KVS triple. aiortc
    honors only one TURN URI per connection anyway; emitting one makes that
    visible. A server with no usable URI is dropped with a warning.

    This narrowing is aiortc-only and must not leak to the browser —
    :func:`fetch_ice_servers` still returns every URI, and ``/start`` still hands
    the full list to the client, which tries all of them.

    Args:
        ice_servers: ICE server dicts from :func:`fetch_ice_servers`.

    Returns:
        A list of ``pipecat.transports.smallwebrtc.connection.IceServer``, each
        with a single-string ``urls``.
    """
    from pipecat.transports.smallwebrtc.connection import IceServer

    configured = settings.turn_transport
    preference = TURN_URI_PREFERENCE if configured == "auto" else (configured,)

    built = []
    for index, server in enumerate(ice_servers):
        urls = server["urls"]
        uris = [urls] if isinstance(urls, str) else list(urls)
        chosen = select_turn_uri(uris, preference)
        if chosen is None:
            logger.warning(
                "No TURN URI matching %s for KVS server %d — dropping it (had: %s)",
                configured,
                index,
                ", ".join(uris) or "nothing",
            )
            continue
        logger.info("Using TURN URI %s for KVS server %d", chosen, index)
        built.append(
            IceServer(
                urls=chosen,
                username=server.get("username"),
                credential=server.get("credential"),
            )
        )
    return built


def filter_relay_only_sdp(sdp: str) -> str:
    """
    Strip every non-relay ICE candidate line from an SDP blob.

    This is the sole relay-only enforcement point (aiortc has no relay-only
    transport-policy knob): drops any ``a=candidate:`` line whose type is not
    ``relay`` (i.e. host / srflx / prflx), so VPC-internal addresses never appear
    in the answer sent to the browser. Non-candidate lines are passed through
    unchanged.

    Args:
        sdp: The SDP string (typically the local answer).

    Returns:
        The SDP with only ``typ relay`` candidate lines retained.
    """
    kept: List[str] = []
    for line in sdp.splitlines():
        stripped = line.strip()
        is_candidate = stripped.startswith("a=candidate:") or stripped.startswith(
            "candidate:"
        )
        if is_candidate and "typ relay" not in stripped:
            continue
        kept.append(line)

    # Preserve trailing newline semantics of the original SDP.
    result = "\r\n".join(kept)
    if sdp.endswith("\n"):
        result += "\r\n"
    return result
