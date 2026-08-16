"""
Domain exceptions + FastAPI handlers for the voice kit's control plane.

Why this exists (the CORS lesson): Starlette routes ``Exception`` handlers into
``ServerErrorMiddleware``, which wraps *outside* ``CORSMiddleware`` — so any
unhandled upstream error (botocore, an LLM/TTS provider) becomes a CORS-less 500
that the browser blocks and reports as a meaningless "Network Error"
(middleware reordering cannot fix this; Starlette special-cases the
``Exception``/500 handler). Every upstream failure must therefore be wrapped in
``UpstreamServiceError`` so the response is produced by a registered exception
handler — which runs *inside* the CORS middleware and carries CORS headers.
"""

from typing import Any, Dict, Optional


class VoiceKitError(Exception):
    """Base exception for voice-kit errors, formatted like an API error."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        status_code: int = 500,
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(self.message)


class UpstreamServiceError(VoiceKitError):
    """
    Raised when an upstream dependency we call (AgentCore, KVS, an LLM or TTS
    provider) returns an error.

    Surfaced as a domain exception (rather than a bare unhandled exception) so
    the response is routed through the registered handler — which runs inside
    the CORS middleware and therefore includes CORS headers. Without this, an
    unhandled upstream error becomes a CORS-less 500 that the browser blocks,
    surfacing to the user as a meaningless "Network Error".

    ``upstream_status`` carries the HTTP status the upstream service itself
    returned (distinct from ``status_code``, our response to the client). Retry
    policy keys off it: 429 and 5xx are transient and retriable, other 4xx are
    deterministic and must fail fast. ``None`` (no HTTP status, e.g. a malformed
    body) is treated as non-retriable.

    HTTP Status: 502 Bad Gateway
    """

    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        upstream_status: Optional[int] = None,
    ):
        super().__init__(
            code="UPSTREAM_SERVICE_ERROR",
            message=message,
            details=details,
            status_code=502,
        )
        self.upstream_status = upstream_status

    @property
    def is_retriable(self) -> bool:
        """Whether the upstream failure is transient (429 or 5xx)."""
        return self.upstream_status is not None and (
            self.upstream_status == 429 or self.upstream_status >= 500
        )


def register_exception_handlers(app) -> None:
    """
    Install a JSON handler for :class:`VoiceKitError` on a FastAPI app.

    Registered handlers run INSIDE ``CORSMiddleware``, so the 502 reaches the
    browser with CORS headers intact (see module docstring). Hosts that already
    route custom exceptions through their own handler can skip this and register
    ``VoiceKitError`` there instead — the important part is that it is a
    *registered* handler, not the unhandled-Exception path.

    Args:
        app: The FastAPI application hosting the voice control-plane router.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    async def voice_kit_error_handler(
        request: Request, exc: VoiceKitError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    app.add_exception_handler(VoiceKitError, voice_kit_error_handler)
