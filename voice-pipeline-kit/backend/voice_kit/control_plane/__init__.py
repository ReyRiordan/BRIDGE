"""
Control-plane side of the kit: the FastAPI signaling router + AgentCore invoke
helper. Runs in the host's API process (e.g. a Lambda) — pipecat-free; needs
only fastapi, pydantic(-settings), and boto3.
"""

from .agentcore import invoke_voice_runtime
from .router import create_voice_router
from .schemas import (
    SessionEndResponse,
    SessionStartResponse,
    SignalRequest,
    SignalResponse,
)

__all__ = [
    "create_voice_router",
    "invoke_voice_runtime",
    "SessionEndResponse",
    "SessionStartResponse",
    "SignalRequest",
    "SignalResponse",
]
