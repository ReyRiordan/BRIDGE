"""
Kinesis Video Streams ICE/TURN helpers for the voice runtime.

The voice runtime never ships STUN/TURN servers to the browser. Instead it asks
KVS for managed TURN credentials (``GetIceServerConfig``), configures the aiortc
peer connection to use **relay only**, and strips every non-relay candidate from
the SDP answer before returning it. The browser therefore connects exclusively
over KVS-managed TURN (UDP), and VPC-internal host candidates (which the browser
could never reach) never leak into signaling.

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
from typing import List

from .config import settings

logger = logging.getLogger(__name__)


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

    Args:
        ice_servers: ICE server dicts from :func:`fetch_ice_servers`.

    Returns:
        A list of ``pipecat.transports.smallwebrtc.connection.IceServer``.
    """
    from pipecat.transports.smallwebrtc.connection import IceServer

    return [
        IceServer(
            urls=s["urls"],
            username=s.get("username"),
            credential=s.get("credential"),
        )
        for s in ice_servers
    ]


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
