"""
Control-plane Lambda entry point: `/health`, `/scenario`, and the voice_kit
signaling router — the thin, pipecat-free half of the BRIDGE backend.

Production serves this app with uvicorn under the AWS Lambda Web Adapter
(api/Dockerfile.api), so `handler` below is not what the Function URL invokes:
Mangum is retained as the zip-packaging fallback. Keeping this module free of
startup/lifespan dependencies is therefore a constraint of that fallback, not a
hard one.

CORS is owned here, not by the Function URL: configuring both duplicates the
response headers and browsers reject them.
"""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from voice_kit import create_voice_router, register_exception_handlers

from api import scenario


def allowed_origins() -> list[str]:
    """Browser origins from the comma-separated ALLOWED_ORIGINS set by infra."""
    return [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]


async def authorize(request: Request, session_id: str) -> None:
    """
    TODO(auth): deliberate no-op — the three /voice endpoints are OPEN.

    Anyone who can reach the Function URL can attach to any session's runtime
    or end it. Acceptable only while the app is a closed pilot behind an
    unpublished URL; before any real rollout this must verify the caller and
    their ownership of `session_id` (see the auth checklist item in
    docs/backend/voice-kit/01-integration-guide.md).
    """


app = FastAPI(title="BRIDGE control plane")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 502s (not CORS-less 500s) for upstream failures — voice-kit gotcha #22.
register_exception_handlers(app)

app.include_router(scenario.router)
# No on_start/on_end: there is no session store — game state lives in the
# runtime container and transcripts stream to the browser live over the data
# channel, so /end returns transcript: [].
app.include_router(create_voice_router(prefix="/voice", authorize=authorize))


@app.get("/health")
def health() -> dict:
    """Liveness. Scenario bundling is proven at import time by api.scenario."""
    return {"status": "ok", "scenario_loaded": True}


# Zip-packaging fallback (see the module docstring) — unused under LWA.
handler = Mangum(app)
