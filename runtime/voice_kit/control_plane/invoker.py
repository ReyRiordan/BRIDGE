"""
The Invoker interface: how the control plane reaches the voice runtime.

The signaling router does not care where the runtime lives — it only needs to
relay an SDP offer and (best-effort) an end-of-session teardown. This module
defines that seam as an abstract ``Invoker`` plus ``get_invoker()``, which
dispatches on ``settings.voice_invoker``:

- ``"agentcore"`` (default): :class:`~.agentcore.AgentCoreInvoker` — boto3
  ``invoke_agent_runtime`` against the deployed AgentCore runtime.
- ``"local"``: :class:`~.local.LocalInvoker` — a plain HTTP POST to a
  localhost ``/invocations`` for running the whole stack on one machine
  (implied by ``BRIDGE_LOCAL=1``; see docs/backend/local-dev.md).

Both methods are async: implementations must not block the event loop
(``AgentCoreInvoker`` wraps its sync boto3 call in ``asyncio.to_thread``).
"""

from abc import ABC, abstractmethod

from ..config import settings

__all__ = ["Invoker", "get_invoker"]


class Invoker(ABC):
    """One WebRTC signaling relay + teardown channel to the voice runtime."""

    @abstractmethod
    async def signal(
        self,
        session_id: str,
        runtime_session_id: str,
        sdp: str,
        type: str = "offer",
    ) -> dict:
        """
        Relay one SDP offer to the runtime and return its answer.

        Args:
            session_id: Host session id (the runtime's only context pointer).
            runtime_session_id: Affinity key pinning the call to one container.
            sdp: The browser's non-trickle SDP offer.
            type: The SDP type.

        Returns:
            dict: The runtime's SDP answer, e.g. ``{"sdp": ..., "type": "answer"}``.

        Raises:
            UpstreamServiceError: If the runtime cannot be reached or cannot
                negotiate — never a bare transport exception (CORS lesson).
        """

    @abstractmethod
    async def end(self, session_id: str, runtime_session_id: str) -> dict:
        """
        Tell the runtime to tear down the session's pipeline.

        Best-effort by contract: the router logs and continues on failure (the
        pipeline idle timeout is the backstop), but implementations still raise
        ``UpstreamServiceError`` so callers can distinguish outcomes.
        """


def get_invoker() -> Invoker:
    """Build the invoker selected by ``settings.voice_invoker``."""
    if settings.voice_invoker == "local":
        from .local import LocalInvoker

        return LocalInvoker()
    from .agentcore import AgentCoreInvoker

    return AgentCoreInvoker()
