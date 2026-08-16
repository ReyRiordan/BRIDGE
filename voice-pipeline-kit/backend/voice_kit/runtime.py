"""
Voice runtime entrypoint — Pipecat pipeline on AWS Bedrock AgentCore Runtime.

This is the container entrypoint built by ``Dockerfile.voice``. The voice
runtime is a long-running, stateful WebRTC server: it instantiates a
``BedrockAgentCoreApp`` that exposes ``/ping`` + ``/invocations`` on :8080 and
is driven by ``invoke_agent_runtime`` from the control plane (the ``/signal``
proxy in ``voice_kit.control_plane.router``).

Host apps normally wrap this module (see docs/01-integration-guide.md): a tiny
``my_voice_app.py`` that registers a context provider + transcript handler
(``voice_kit.context``) and re-exports ``app``. Running this module directly
uses the default static context provider.

Per-invoke flow (``@app.entrypoint``):
  1. Receive ``{session_id, sdp, type}`` (the only context pointer — everything
     else is resolved by the registered context provider).
  2. Hand the SDP offer to a ``SmallWebRTCRequestHandler`` configured with the
     KVS managed-TURN ICE servers (lazily fetched). The handler owns the aiortc
     peer connection and SDP negotiation.
  3. In the handler's connection callback, build the Pipecat pipeline + a
     ``SmallWebRTCTransport`` around the connection and launch the pipeline on the
     runtime's event loop. Transcript turns are pushed to the browser via
     ``connection.send_app_message``.
  4. Return the **relay-only filtered** answer SDP from ``get_answer()``.

Operational lessons honored:
  - **Loop runs forever in a background thread.** A WebRTC pipeline must keep
    pumping media after the invoke returns its SDP answer, so the asyncio loop
    cannot be a one-shot ``run_until_complete``. We start it with
    ``run_forever`` in a daemon thread at import and submit coroutines via
    ``run_coroutine_threadsafe``. Pipelines stay bound to that one loop across
    invokes. Any sync I/O a host sink does must be wrapped in
    ``asyncio.to_thread`` — no blocking client may be bound to the loop.
  - **uvicorn ``--workers 1``** (Dockerfile.voice): peer-connection state lives in
    this process's memory and must not be sharded across workers.
  - **Lazy KVS init**: ICE config is fetched per session, never at import.

APIs verified against pipecat-ai 1.3.0: ``SmallWebRTCRequestHandler``,
``SmallWebRTCTransport(webrtc_connection, params)``, ``TransportParams``, and
``PipelineRunner().run(worker)``.
"""

import asyncio
import concurrent.futures
import logging
import threading

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .kvs import build_ice_servers, fetch_ice_servers, filter_relay_only_sdp
from .pipeline import build_pipeline_for_session

logger = logging.getLogger(__name__)

# A single asyncio loop runs forever in a daemon thread for the life of the
# container. WebRTC pipelines launched in a connection callback keep running on
# it after handle_offer returns the SDP answer.
_loop = asyncio.new_event_loop()


def _start_loop() -> None:
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


_loop_thread = threading.Thread(target=_start_loop, name="voice-loop", daemon=True)
_loop_thread.start()

# AgentCore app — exposes /ping + /invocations on :8080.
app = BedrockAgentCoreApp()

# Active pipeline asyncio tasks keyed by session id. Shared in-process state —
# the reason Dockerfile.voice runs uvicorn with --workers 1.
_pipeline_tasks: dict = {}

# Offer negotiation (KVS ICE fetch + SDP round-trip) is sub-second when healthy;
# a stalled KVS call must not block the handler thread forever.
OFFER_TIMEOUT_SECONDS = 30


@app.entrypoint
def handle_offer(payload: dict):
    """AgentCore entrypoint: SDP offer in → relay-only SDP answer out.

    Synchronous shim: submits the async handler to the persistent loop (running
    in the background thread) and blocks for the SDP answer. The pipeline it
    starts keeps running on that loop after this returns.
    """
    future = asyncio.run_coroutine_threadsafe(_handle_offer(payload), _loop)
    try:
        return future.result(timeout=OFFER_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        future.cancel()
        session_id = payload.get("session_id")
        logger.error("Offer negotiation timed out for session %s", session_id)
        return {"error": "voice runtime timed out negotiating the connection"}


async def _handle_offer(payload: dict) -> dict:
    """Negotiate the WebRTC connection, start the pipeline, return the answer."""
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.request_handler import (
        SmallWebRTCRequest,
        SmallWebRTCRequestHandler,
    )
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    session_id = payload["session_id"]
    sdp = payload["sdp"]
    sdp_type = payload.get("type", "offer")

    # Lazy KVS managed-TURN ICE servers for this session.
    ice_servers = build_ice_servers(fetch_ice_servers())
    handler = SmallWebRTCRequestHandler(ice_servers=ice_servers)

    async def on_connection(connection) -> None:
        """Build the transport + pipeline and launch it for this connection.

        Runs inside handle_web_request, before get_answer() — so the transport's
        output audio track is wired into the connection and advertised in the SDP
        answer (no silent, audio-less answer).
        """
        # Audio in is resampled to 16 kHz to match the STT path; both directions
        # enabled so the agent voice is sent back over WebRTC.
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=16000,
            ),
        )

        # Push each finalized transcript turn to the browser over the data channel.
        def emit(message_json: str) -> None:
            connection.send_app_message(message_json)

        context = await build_pipeline_for_session(session_id, transport, emit=emit)
        _loop.create_task(_run_task(context.task, session_id))

    answer = await handler.handle_web_request(
        SmallWebRTCRequest(sdp=sdp, type=sdp_type),
        webrtc_connection_callback=on_connection,
    )
    if not answer:
        raise RuntimeError("voice runtime produced no SDP answer")

    filtered_sdp = filter_relay_only_sdp(answer["sdp"])
    logger.info("Returning relay-only answer for session %s", session_id)
    return {"sdp": filtered_sdp, "type": answer["type"]}


async def _run_task(task, session_id: str) -> None:
    """Run a pipeline task to completion via the Pipecat runner, then clean up.

    Only one pipeline may run per session: a reconnect that lands on the same
    warm container would otherwise leave the old pipeline alive, both feeding
    the transcript sink (duplicated turns). Any existing task is cancelled and
    awaited before this one registers, and cleanup pops the registration only if
    it still holds this task, so a superseded pipeline's cleanup can never
    remove its successor's entry.
    """
    from pipecat.pipeline.runner import PipelineRunner

    this_task = asyncio.current_task()
    old_task = _pipeline_tasks.get(session_id)
    if old_task is not None and old_task is not this_task:
        logger.info("Cancelling existing pipeline for session %s", session_id)
        old_task.cancel()
        try:
            await old_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning(
                "Superseded pipeline for session %s raised during cancellation",
                session_id,
                exc_info=True,
            )
    _pipeline_tasks[session_id] = this_task
    try:
        # handle_sigint/sigterm=False: the runner installs signal handlers via
        # loop.add_signal_handler, which only works on the main thread. Our loop
        # runs in a daemon thread (run_forever), so signal handling must be off
        # or the pipeline dies with "set_wakeup_fd only works in main thread".
        await PipelineRunner(handle_sigint=False, handle_sigterm=False).run(task)
    except asyncio.CancelledError:
        logger.info("Pipeline for session %s cancelled", session_id)
        raise
    except Exception as e:
        logger.error("Pipeline for session %s failed: %s", session_id, e, exc_info=True)
    finally:
        if _pipeline_tasks.get(session_id) is this_task:
            _pipeline_tasks.pop(session_id)
        logger.info("Pipeline finished for session %s", session_id)


if __name__ == "__main__":
    # AgentCore runs the app via uvicorn from Dockerfile.voice (--workers 1); this
    # block supports a local `python -m voice_kit.runtime` smoke run.
    app.run(host="0.0.0.0", port=8080)
