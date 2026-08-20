"""
Localhost invoker for the voice runtime — the local dev loop.

Same seam, no AWS: instead of ``invoke_agent_runtime``, this POSTs the very same
payloads to the runtime's ``/invocations`` on ``settings.voice_runtime_url``
(the AgentCore app serves that route itself, so the runtime code path is
identical to the deployed one). Selected by ``VOICE_INVOKER=local``, which
``BRIDGE_LOCAL=1`` implies.

``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`` is sent for envelope parity with
``invoke_agent_runtime(runtimeSessionId=...)``. Locally there is exactly one
container, so affinity is free — the header keeps the two paths readable side by
side (and lets the runtime log the id).

aiohttp is imported lazily inside the calls: it is already a voice_kit dep, but
deferring keeps the Lambda's import path as light as the boto3 one.
"""

import json
import logging
from typing import Optional

from ..config import settings
from ..errors import UpstreamServiceError
from .invoker import Invoker

logger = logging.getLogger(__name__)

# Must EXCEED the runtime's OFFER_TIMEOUT_SECONDS (30): when negotiation stalls
# the runtime answers 200 with an in-band {"error": ...}, and a shorter client
# timeout would replace that precise message with a generic transport failure.
REQUEST_TIMEOUT_SECONDS = 35


class LocalInvoker(Invoker):
    """
    Invoker backed by a plain HTTP POST to a locally running voice runtime.

    Args:
        base_url: Runtime base URL; defaults to ``settings.voice_runtime_url``.
    """

    def __init__(self, base_url: Optional[str] = None):
        self._base_url = base_url

    async def signal(
        self,
        session_id: str,
        runtime_session_id: str,
        sdp: str,
        type: str = "offer",
    ) -> dict:
        """Relay one SDP offer. No ``action`` key — the entrypoint defaults to
        ``"signal"``, exactly as ``AgentCoreInvoker`` sends it."""
        return await self._invoke(
            runtime_session_id,
            {"session_id": session_id, "sdp": sdp, "type": type},
        )

    async def end(self, session_id: str, runtime_session_id: str) -> dict:
        """Tear the session's pipeline down (``action: "end"``)."""
        return await self._invoke(
            runtime_session_id,
            {"session_id": session_id, "action": "end"},
        )

    async def _invoke(self, runtime_session_id: str, payload: dict) -> dict:
        import aiohttp

        url = (
            f"{(self._base_url or settings.voice_runtime_url).rstrip('/')}/invocations"
        )
        session_id = payload.get("session_id")

        # A fresh ClientSession per call: a module-level one binds to the loop
        # that created it, which dies under Mangum (per-invoke loop) and under
        # uvicorn --reload. Two requests per conversation makes the cost moot.
        #
        # Only the transport block is wrapped by the broad except — the raises
        # below must not be re-wrapped into a "could not be reached" message.
        try:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.post(
                    url,
                    json=payload,
                    headers={
                        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": runtime_session_id
                    },
                ) as response:
                    status = response.status
                    body = await response.text()
        except Exception as e:  # noqa: BLE001 - never leak a bare transport error
            logger.exception(
                "Local voice runtime invoke failed for session %s (url=%s)",
                session_id,
                url,
            )
            raise UpstreamServiceError(
                message="The voice service could not be reached.",
                details={"error": str(e), "url": url},
            )

        if status >= 400:
            raise UpstreamServiceError(
                message="The voice service could not be reached.",
                details={"error": body[:500], "url": url},
                upstream_status=status,
            )

        try:
            answer = json.loads(body)
        except ValueError as e:
            raise UpstreamServiceError(
                message="The voice service returned a malformed response.",
                details={"error": str(e), "body": body[:500]},
            )

        logger.info(
            "Invoked local voice runtime for session %s (runtime_session_id=%s)",
            session_id,
            runtime_session_id,
        )

        # Same in-band error contract as AgentCoreInvoker: the runtime answers
        # 200 with {"error": ...} when it cannot negotiate (e.g. offer timeout).
        if isinstance(answer, dict) and "error" in answer:
            raise UpstreamServiceError(
                message="The voice service could not negotiate the connection.",
                details={"error": answer["error"]},
            )
        return answer
