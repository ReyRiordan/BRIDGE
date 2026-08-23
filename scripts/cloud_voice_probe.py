#!/usr/bin/env python3
"""
Post-deploy WebRTC probe for the DEPLOYED stack — the cloud counterpart to
`scripts/local_voice_smoke.py`.

Local mode proves the game logic on loopback host candidates and deliberately
skips the TURN path entirely, so it can never tell you whether the deployed
runtime can reach the browser. This script drives the same three control-plane
endpoints against the real function URL, with real KVS TURN credentials, and
gates on the thing that actually breaks in the cloud: relay candidates in the
runtime's answer.

    python3 scripts/cloud_voice_probe.py                       # amplify_outputs.json
    python3 scripts/cloud_voice_probe.py --api-base https://...lambda-url...aws/

What it proves, in order:
  1. `/start` returns a valid `runtime_session_id` and a NON-EMPTY `ice_servers`
     list. Every TURN URI is printed — the live record of what KVS hands out.
  2. Control: this machine gathers a `typ relay` candidate with those same
     credentials. If the laptop gets an allocation and the runtime does not, the
     URIs and creds are fine and the failure is specific to AgentCore's network.
  3. The runtime's answer carries at least one `typ relay` candidate and zero
     non-relay ones. Both halves matter: the old runbook grep
     (`grep 'a=candidate' | grep -v relay` comes back empty) passed vacuously on
     an answer with ZERO candidates, which is exactly what the Aug 20 outage
     looked like for three days.
  4. ICE reaches `connected` over the relay.
  5. A v1 game event arrives on the data channel.
  6. `/end` tears the session down.

This starts a real, billable AgentCore session, so `/end` runs in a `finally`
even when an assertion fails. Exits non-zero on the first failure, so it can
gate a deploy directly.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

# scripts/ is not a package, but the script's own directory is on sys.path when
# it runs, so the local smoke's helpers import directly. Nothing is duplicated:
# the two probes share the reporting helpers, the silent track and the POST
# wrapper, and differ only in what they assert.
from local_voice_smoke import _post, fail, ok, silent_audio_track

REPO_ROOT = Path(__file__).resolve().parent.parent
AMPLIFY_OUTPUTS = REPO_ROOT / "amplify_outputs.json"

# Cloud budgets, all looser than the local smoke's: a cold AgentCore container
# plus a TURN allocation over the internet is not a loopback handshake.
GATHER_TIMEOUT = 20
ICE_CONNECT_TIMEOUT = 30
EVENT_TIMEOUT = 20

# AgentCore's invoke_agent_runtime rejects a runtimeSessionId outside this range
# client-side, before any network call (voice_kit gotcha #5).
RUNTIME_SESSION_ID_BOUNDS = (33, 256)


def default_api_base() -> str:
    """Read `custom.apiUrl` out of the repo-root amplify_outputs.json."""
    if not AMPLIFY_OUTPUTS.exists():
        fail(
            f"{AMPLIFY_OUTPUTS} not found — pass --api-base with the deployed "
            "function URL"
        )
    outputs = json.loads(AMPLIFY_OUTPUTS.read_text())
    api_url = outputs.get("custom", {}).get("apiUrl")
    if not api_url:
        fail(f"{AMPLIFY_OUTPUTS} has no custom.apiUrl — pass --api-base")
    return api_url


def candidate_lines(sdp: str) -> list[str]:
    return [
        line.strip()
        for line in sdp.splitlines()
        if line.strip().startswith(("a=candidate:", "candidate:"))
    ]


def build_peer_connection(ice_servers: list[dict]):
    """An aiortc peer connection configured with the KVS TURN servers."""
    from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

    configuration = RTCConfiguration(
        iceServers=[
            RTCIceServer(
                urls=server["urls"],
                username=server.get("username"),
                credential=server.get("credential"),
            )
            for server in ice_servers
        ]
    )
    # aiortc has no `iceTransportPolicy: 'relay'` knob, so every relay assertion
    # below reads candidate lines instead of trusting a policy.
    return RTCPeerConnection(configuration)


async def probe(api_base: str) -> None:
    import aiohttp
    from aiortc import RTCSessionDescription

    session_id = str(uuid.uuid4())
    print(f"Cloud voice probe — {api_base}")
    print(f"  session {session_id}\n")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as http:
        # --- 1. start -------------------------------------------------------
        start = await _post(http, api_base, f"/voice/{session_id}/start")
        runtime_session_id = start.get("runtime_session_id", "")
        low, high = RUNTIME_SESSION_ID_BOUNDS
        if not low <= len(runtime_session_id) <= high:
            fail(
                f"runtime_session_id is {len(runtime_session_id)} chars — AgentCore "
                f"requires {low}-{high}"
            )
        ok(f"/start minted a {len(runtime_session_id)}-char runtime_session_id")

        ice_servers = start.get("ice_servers") or []
        if not ice_servers:
            fail(
                "/start returned an EMPTY ice_servers list — the KVS fetch failed. "
                "Check the API role's KVS grants and KVS_CHANNEL_NAME, and the "
                "control-plane log for the warning."
            )
        uri_count = 0
        for index, server in enumerate(ice_servers):
            urls = server["urls"]
            for uri in [urls] if isinstance(urls, str) else urls:
                print(f"    server {index}: {uri}")
                uri_count += 1
        ok(f"/start returned {len(ice_servers)} KVS server(s), {uri_count} TURN URI(s)")

        pc = build_peer_connection(ice_servers)
        try:
            # --- 2. offer ---------------------------------------------------
            pc.addTrack(silent_audio_track())
            # The runtime's pipeline blocks until the data channel is established.
            channel = pc.createDataChannel("data")
            events = []

            @channel.on("message")
            def _on_message(message):
                events.append(message)

            await pc.setLocalDescription(await pc.createOffer())
            deadline = time.monotonic() + GATHER_TIMEOUT
            while pc.iceGatheringState != "complete":
                if time.monotonic() > deadline:
                    fail("ICE gathering never completed (non-trickle needs it)")
                await asyncio.sleep(0.1)

            # The control. Same credentials, same URIs, a machine that is NOT
            # AgentCore: a relay candidate here means the TURN side is healthy
            # and any runtime-side failure below is about its network.
            own = candidate_lines(pc.localDescription.sdp)
            own_relay = [c for c in own if "typ relay" in c]
            if not own_relay:
                fail(
                    f"this machine gathered NO relay candidate ({len(own)} candidates "
                    "total) — the TURN URIs or credentials themselves are bad, so "
                    "the runtime-side result below proves nothing"
                )
            ok(
                f"control: this machine holds a TURN allocation ({len(own_relay)} relay)"
            )

            # --- 3. signal --------------------------------------------------
            answer = await _post(
                http,
                api_base,
                f"/voice/{session_id}/signal",
                {
                    "runtime_session_id": runtime_session_id,
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type,
                },
            )
            candidates = candidate_lines(answer["sdp"])
            for line in candidates:
                print(f"    {line}")
            relay = [c for c in candidates if "typ relay" in c]
            non_relay = [c for c in candidates if "typ relay" not in c]

            if not candidates:
                fail(
                    "the runtime's answer carries ZERO ICE candidates — it gathered "
                    "no relay candidate at all and filter_relay_only_sdp stripped "
                    "what was left. The browser will sit at 'checking' until it "
                    "times out. Check the runtime log for a ~5.0s flat gathering "
                    "window, which is aioice's hardcoded timeout on TURN-over-UDP "
                    "requests that never get an answer."
                )
            if non_relay:
                fail(
                    f"{len(non_relay)} non-relay candidate(s) in the answer — the "
                    "relay-only SDP filter did not run; internal addresses are "
                    "leaking to the browser"
                )
            ok(f"answer carries {len(relay)} relay candidate(s), 0 non-relay")

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
            )

            # --- 4. connect -------------------------------------------------
            deadline = time.monotonic() + ICE_CONNECT_TIMEOUT
            while pc.iceConnectionState not in ("connected", "completed"):
                if pc.iceConnectionState == "failed":
                    fail("ICE failed over the relay")
                if time.monotonic() > deadline:
                    fail(f"ICE stalled in state {pc.iceConnectionState!r}")
                await asyncio.sleep(0.25)
            ok(f"ICE {pc.iceConnectionState} over the KVS relay")

            # --- 5. data channel --------------------------------------------
            deadline = time.monotonic() + EVENT_TIMEOUT
            while not events:
                if time.monotonic() > deadline:
                    fail("no game event arrived on the data channel")
                await asyncio.sleep(0.25)
            event = json.loads(events[0])
            if (
                not isinstance(event, dict)
                or event.get("v") != 1
                or "type" not in event
            ):
                fail(f"data channel payload is not a v1 envelope: {event!r}")
            ok(f"data channel delivered a v1 {event['type']} event")
        finally:
            # A real AgentCore session is running and billing regardless of how
            # we got here, so tear it down on the failure path too.
            await pc.close()
            try:
                async with http.post(
                    f"{api_base}/voice/{session_id}/end",
                    json={"runtime_session_id": runtime_session_id},
                ) as response:
                    body = await response.text()
                if response.status >= 400:
                    print(
                        f"  ! /end returned {response.status}: {body[:200]}",
                        file=sys.stderr,
                    )
                else:
                    ok("/end tore the session down")
            except Exception as e:  # noqa: BLE001 - teardown must not mask the result
                print(f"  ! /end failed: {e}", file=sys.stderr)

    print("\nCloud voice probe PASSED — relay-only ICE connected end to end.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-base",
        default=None,
        help="Control-plane base URL (default: custom.apiUrl from amplify_outputs.json)",
    )
    args = parser.parse_args()
    api_base = args.api_base or default_api_base()
    asyncio.run(probe(api_base.rstrip("/")))


if __name__ == "__main__":
    main()
