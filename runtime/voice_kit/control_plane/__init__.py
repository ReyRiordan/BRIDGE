"""
Control-plane side of the kit: the FastAPI signaling router + AgentCore invoke
helper. Runs in the host's API process (e.g. a Lambda) — pipecat-free; needs
only fastapi, pydantic(-settings), and boto3.
"""

from .agentcore import AgentCoreInvoker
from .invoker import Invoker, get_invoker
from .router import create_voice_router
from .schemas import (
    SessionEndRequest,
    SessionEndResponse,
    SessionStartResponse,
    SignalRequest,
    SignalResponse,
)

__all__ = [
    "AgentCoreInvoker",
    "Invoker",
    "create_voice_router",
    "get_invoker",
    "SessionEndRequest",
    "SessionEndResponse",
    "SessionStartResponse",
    "SignalRequest",
    "SignalResponse",
]
