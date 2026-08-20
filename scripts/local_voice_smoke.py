#!/usr/bin/env python3
"""
Tier-1 smoke test for local dev mode: a real WebRTC handshake against a running
`npm run dev`, with zero AWS calls.

It drives the same three control-plane endpoints the browser does, using
aiortc as the peer (already installed via runtime/requirements-voice.txt), so
it proves the transport independently of the SPA — run it first when a browser
session will not connect.

    npm run dev                                  # in another terminal
    .venv/bin/python scripts/local_voice_smoke.py

What it proves, in order:
  1. `/start` returns `ice_servers: []` — the KVS fetch was skipped, so no boto3
     call was made.
  2. The runtime's answer contains a NON-relay candidate — the relay-only SDP
     filter was skipped (in deploy, every candidate is `typ relay`).
  3. ICE reaches `connected` on loopback host candidates.
  4. A v1 game event arrives over the data channel and parses to an OBJECT —
     the check that catches a double-encoded payload, which leaves audio
     working and every event silently dropped by the browser's reducer.
  5. `/end` tears the session down cleanly.

Exits non-zero on the first failure. Deliberately NOT wired into CI: it needs
three live processes and the provider API keys. And it is never a substitute
for the relay-only post-deploy gate in the deploy runbook — local mode proves
the game logic, not the cloud's TURN path.
"""

import argparse
import asyncio
import fractions
import json
import sys
import time
import uuid

DEFAULT_API_BASE = "http://127.0.0.1:8000"
ICE_CONNECT_TIMEOUT = 20
GATHER_TIMEOUT = 10
# The runtime's clock ticks at 1 Hz, so a few seconds is plenty of margin.
EVENT_TIMEOUT = 10

# 20 ms of 48 kHz mono silence per frame — the runtime's VAD needs a real audio
# track to attach to, not what it carries.
SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 960


def ok(message: str) -> None:
    print(f"  \033[32m✓\033[0m {message}")


def fail(message: str) -> None:
    print(f"  \033[31m✗\033[0m {message}", file=sys.stderr)
    sys.exit(1)


def silent_audio_track():
    """Build a MediaStreamTrack emitting silence (aiortc imported lazily)."""
    import av
    from aiortc import MediaStreamTrack

    class _Silence(MediaStreamTrack):
        kind = "audio"

        def __init__(self):
            super().__init__()
            self._timestamp = 0

        async def recv(self):
            frame = av.AudioFrame(
                format="s16", layout="mono", samples=SAMPLES_PER_FRAME
            )
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            frame.pts = self._timestamp
            frame.sample_rate = SAMPLE_RATE
            frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
            self._timestamp += SAMPLES_PER_FRAME
            # Pace the track so it behaves like a live microphone.
            await asyncio.sleep(SAMPLES_PER_FRAME / SAMPLE_RATE)
            return frame

    return _Silence()


async def _post(session, api_base: str, path: str, body=None) -> dict:
    async with session.post(f"{api_base}{path}", json=body) as response:
        text = await response.text()
        if response.status >= 400:
            fail(f"POST {path} -> {response.status}: {text[:300]}")
        return json.loads(text)


async def run(api_base: str) -> None:
    import aiohttp
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

    session_id = str(uuid.uuid4())
    print(f"Local voice smoke — session {session_id}")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as http:
        # --- 1. start -------------------------------------------------------
        start = await _post(http, api_base, f"/voice/{session_id}/start")
        if start.get("ice_servers") != []:
            fail(f"expected no ICE servers in local mode, got {start['ice_servers']!r}")
        ok("/start returned ice_servers: [] (KVS fetch skipped)")
        runtime_session_id = start["runtime_session_id"]

        # --- 2. offer -------------------------------------------------------
        # No ICE servers and policy 'all': the browser's local-mode config.
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
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
        ok("ICE gathering complete (non-trickle offer ready)")

        # --- 3. signal ------------------------------------------------------
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
        candidates = [
            line
            for line in answer["sdp"].splitlines()
            if line.strip().startswith(("a=candidate:", "candidate:"))
        ]
        if not candidates:
            fail("the answer carried no ICE candidates at all")
        if all("typ relay" in c for c in candidates):
            fail(
                "every answer candidate is 'typ relay' — the relay-only filter "
                "ran, so this is NOT local mode"
            )
        ok(f"answer carries a non-relay candidate ({len(candidates)} total)")

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )

        # --- 4. connect -----------------------------------------------------
        deadline = time.monotonic() + ICE_CONNECT_TIMEOUT
        while pc.iceConnectionState not in ("connected", "completed"):
            if pc.iceConnectionState == "failed":
                fail("ICE failed")
            if time.monotonic() > deadline:
                fail(f"ICE stalled in state {pc.iceConnectionState!r}")
            await asyncio.sleep(0.25)
        ok(f"ICE {pc.iceConnectionState} on host candidates")

        # --- 5. data channel ------------------------------------------------
        deadline = time.monotonic() + EVENT_TIMEOUT
        while not events:
            if time.monotonic() > deadline:
                fail("no game event arrived on the data channel")
            await asyncio.sleep(0.25)
        event = json.loads(events[0])
        if not isinstance(event, dict):
            fail(
                f"game event parsed to {type(event).__name__}, not an object — "
                "the payload is double-encoded and the SPA drops every event"
            )
        if event.get("v") != 1 or "type" not in event:
            fail(f"game event is not a v1 envelope: {event!r}")
        ok(f"data channel delivered a v1 {event['type']} event")

        # --- 6. end ---------------------------------------------------------
        await pc.close()
        end = await _post(
            http,
            api_base,
            f"/voice/{session_id}/end",
            {"runtime_session_id": runtime_session_id},
        )
        if "message" not in end:
            fail(f"unexpected /end response: {end!r}")
        ok("/end tore the session down")

    print("\nLocal voice smoke PASSED — zero AWS calls, ICE connected.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help="Control-plane base URL (default: %(default)s)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.api_base.rstrip("/")))


if __name__ == "__main__":
    main()
