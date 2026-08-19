"""
Generic FastAPI router for the three voice signaling endpoints.

The browser cannot reach the AgentCore data-plane directly (no CORS), so the
host API proxies signaling. The host's domain logic (auth, session state
machine, transcript storage) plugs in via three async hooks on the factory —
the router itself imports no pipecat/aiortc and runs in the API process
(Lambda), not the runtime container.

Endpoint contract (consumed by frontend/voiceApi.ts):

    POST {prefix}/{session_id}/start   → SessionStartResponse
    POST {prefix}/{session_id}/signal  → SignalResponse
    POST {prefix}/{session_id}/end     → SessionEndResponse
"""

import asyncio
import logging
import uuid
from typing import Awaitable, Callable, List, Optional, Sequence

from fastapi import APIRouter, Request

from ..config import settings
from ..kvs import fetch_ice_servers
from ..types import TranscriptMessage
from .invoker import Invoker, get_invoker
from .schemas import (
    SessionEndRequest,
    SessionEndResponse,
    SessionStartResponse,
    SignalRequest,
    SignalResponse,
)

logger = logging.getLogger(__name__)

# Hook signatures. All are async; raise the host's own HTTPException (or any
# registered exception) to reject.
AuthorizeHook = Callable[[Request, str], Awaitable[None]]
OnStartHook = Callable[[str], Awaitable[None]]
OnEndHook = Callable[[str], Awaitable[List[TranscriptMessage]]]


def create_voice_router(
    *,
    authorize: Optional[AuthorizeHook] = None,
    on_start: Optional[OnStartHook] = None,
    on_end: Optional[OnEndHook] = None,
    invoker: Optional[Invoker] = None,
    dependencies: Sequence = (),
    prefix: str = "/voice",
    tags: Optional[list] = None,
) -> APIRouter:
    """
    Build the signaling router with the host's domain hooks plugged in.

    Args:
        authorize: ``async (request, session_id) -> None`` — called FIRST in
            all three handlers. Replace this with your ownership/status/state
            checks; raise your own HTTPException (403/404/400) to reject.
            **The default is a no-op: an unauthenticated /signal endpoint lets
            anyone attach to any session's runtime. Do not ship without either
            this hook or FastAPI dependencies enforcing auth.**
        on_start: ``async (session_id) -> None`` — called after authorize in
            the start handler, before the ICE fetch. Put your session state
            transition here (e.g. flip your session to an "in progress" status
            and stamp a start time). Calling start on an already-started
            session should be a no-op in your hook — the endpoint is re-invoked
            on cold-start retry to mint a fresh runtime_session_id.
        on_end: ``async (session_id) -> list[TranscriptMessage]`` — called in
            the end handler. Put your closing state transition here and return
            the final transcript. IMPORTANT ordering lesson: read the
            transcript AFTER your status flip — the runtime writes turns live,
            so an earlier snapshot can miss turns.
        invoker: The channel to the voice runtime (``/signal`` relay + the
            best-effort ``/end`` teardown). Defaults to ``get_invoker()``,
            which dispatches on ``settings.voice_invoker`` — pass one
            explicitly to inject a fake in tests or a custom backend.
        dependencies: FastAPI ``Depends(...)`` objects applied to all three
            routes (e.g. your ``Depends(get_current_user)``); complements or
            replaces ``authorize`` for FastAPI-native auth.
        prefix: Route prefix (default ``/voice``).
        tags: OpenAPI tags.

    Returns:
        The configured ``APIRouter`` — ``app.include_router(...)`` it.
    """
    router = APIRouter(
        prefix=prefix, tags=tags or ["voice"], dependencies=list(dependencies)
    )
    invoker = invoker or get_invoker()

    async def _authorize(request: Request, session_id: str) -> None:
        if authorize is not None:
            await authorize(request, session_id)

    @router.post("/{session_id}/start", response_model=SessionStartResponse)
    async def start_voice_session(session_id: str, request: Request):
        """
        Start (or resume) a voice session.

        Runs the host's authorize + on_start hooks, then mints a fresh AgentCore
        `runtime_session_id` and fetches the browser's own KVS managed-TURN ICE
        servers. No stream is created here: the browser builds a WebRTC offer
        and signals it via POST /{id}/signal, which proxies to the voice
        runtime. Calling this again mid-session just issues a new
        `runtime_session_id` (supports cold-start retry / resume).
        """
        await _authorize(request, session_id)
        if on_start is not None:
            await on_start(session_id)

        # Mint the AgentCore affinity key the frontend pins all signaling to.
        # AgentCore's invoke_agent_runtime requires runtimeSessionId length
        # 33-256 — validated CLIENT-SIDE by botocore, so a too-short id raises
        # ParamValidationError before any network call. uuid4().hex is only 32;
        # the prefix lifts it past the floor. Enforced structurally here.
        runtime_session_id = f"{settings.runtime_session_id_prefix}{uuid.uuid4().hex}"
        if not 33 <= len(runtime_session_id) <= 256:
            raise ValueError(
                "runtime_session_id must be 33-256 chars; adjust "
                f"runtime_session_id_prefix (got {len(runtime_session_id)} chars)"
            )

        # Fetch the browser's own KVS managed-TURN ICE servers. Relay-only WebRTC
        # requires BOTH peers to hold a TURN allocation; the browser builds its
        # RTCPeerConnection with these (without them it gathers only private host
        # candidates and the runtime's relay CHANNEL_BIND is rejected → ICE stall).
        # Non-fatal: if KVS is unreachable we still return so the UI can surface it.
        # to_thread: fetch_ice_servers is sync boto3, which would block the
        # event loop under a local uvicorn.
        ice_servers: list = []
        try:
            ice_servers = await asyncio.to_thread(fetch_ice_servers)
        except Exception as e:  # noqa: BLE001 - degrade gracefully, log for diagnosis
            logger.warning(
                "Failed to fetch ICE servers for session %s: %s", session_id, e
            )

        return SessionStartResponse(
            runtime_session_id=runtime_session_id,
            session_id=session_id,
            ice_servers=ice_servers,
        )

    @router.post("/{session_id}/signal", response_model=SignalResponse)
    async def signal_voice_session(
        session_id: str, body: SignalRequest, request: Request
    ):
        """
        Proxy a WebRTC signaling round-trip to the voice runtime.

        The browser cannot reach the AgentCore data-plane directly (no CORS), so
        its SDP offer is relayed here: authorize, then call invoke_voice_runtime
        (IAM/SigV4, pinned to `runtime_session_id`) and return the SDP answer.
        Non-trickle, single round-trip. On a cold-start ICE failure the frontend
        retries with a fresh `runtime_session_id` (same `session_id`).
        """
        await _authorize(request, session_id)

        answer = await invoker.signal(
            session_id=session_id,
            runtime_session_id=body.runtime_session_id,
            sdp=body.sdp,
            type=body.type,
        )

        return SignalResponse(sdp=answer["sdp"], type=answer.get("type", "answer"))

    @router.post("/{session_id}/end", response_model=SessionEndResponse)
    async def end_voice_session(
        session_id: str,
        request: Request,
        body: Optional[SessionEndRequest] = None,
    ):
        """
        End a voice session.

        When the body carries the browser-held `runtime_session_id`, the
        runtime's pipeline teardown is invoked best-effort (`action: "end"`) —
        a failure is logged and the response is still 200, with the pipeline
        idle timeout as the backstop. Then the host's on_end hook runs and its
        transcript is returned. An empty/absent body skips teardown.
        """
        await _authorize(request, session_id)

        if body is not None and body.runtime_session_id:
            try:
                await invoker.end(
                    session_id=session_id,
                    runtime_session_id=body.runtime_session_id,
                )
            except Exception as e:  # noqa: BLE001 - best-effort teardown
                logger.warning(
                    "Voice runtime teardown failed for session %s "
                    "(runtime_session_id=%s): %s",
                    session_id,
                    body.runtime_session_id,
                    e,
                )

        transcript: List[TranscriptMessage] = []
        if on_end is not None:
            transcript = await on_end(session_id)

        return SessionEndResponse(
            message="Session ended successfully",
            transcript=transcript,
        )

    return router
