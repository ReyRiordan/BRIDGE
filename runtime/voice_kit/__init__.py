"""
voice_kit — a self-contained voice-to-voice pipeline:

    browser WebRTC → Bedrock AgentCore runtime → pipecat
        (STT: Transcribe/Together → LLM: OpenRouter/Bedrock → TTS: Inworld/Polly)

Two halves, one package:

- **Control plane** (this public surface): the FastAPI signaling router and its
  hooks — runs in the host API process; pipecat-free.
- **Runtime** (``voice_kit.runtime`` / ``.pipeline`` / ``.processors``): the
  AgentCore container entrypoint — deliberately NOT imported here, because it
  pulls pipecat and the API host does not install it.

Start with docs/01-integration-guide.md.
"""

from .config import ReasoningEffort, configure, settings
from .context import (
    ContextProvider,
    ProcessorFactory,
    ProcessorFactoryArgs,
    SessionContext,
    SessionEndHook,
    SessionStartHook,
    TranscriptHandler,
    get_processor_factory,
    get_session_end_hook,
    get_session_start_hook,
    set_context_provider,
    set_processor_factory,
    set_session_end_hook,
    set_session_start_hook,
    set_transcript_handler,
)
from .control_plane.agentcore import AgentCoreInvoker
from .control_plane.invoker import Invoker, get_invoker
from .control_plane.router import create_voice_router
from .errors import UpstreamServiceError, VoiceKitError, register_exception_handlers
from .types import IceServerConfig, TranscriptMessage, VoiceConfig

__all__ = [
    "ReasoningEffort",
    "configure",
    "settings",
    "ContextProvider",
    "ProcessorFactory",
    "ProcessorFactoryArgs",
    "SessionContext",
    "SessionEndHook",
    "SessionStartHook",
    "TranscriptHandler",
    "get_processor_factory",
    "get_session_end_hook",
    "get_session_start_hook",
    "set_context_provider",
    "set_processor_factory",
    "set_session_end_hook",
    "set_session_start_hook",
    "set_transcript_handler",
    "AgentCoreInvoker",
    "Invoker",
    "get_invoker",
    "create_voice_router",
    "UpstreamServiceError",
    "VoiceKitError",
    "register_exception_handlers",
    "IceServerConfig",
    "TranscriptMessage",
    "VoiceConfig",
]
