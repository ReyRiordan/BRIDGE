"""
Extension points: how the host app plugs its domain into the pipeline.

The AgentCore invoke payload carries only ``{session_id, sdp, type}`` — the
"pointer + re-fetch" contract. Everything else the pipeline needs (system
prompt, TTS voice, prior history, time limit) must be derivable from that one
``session_id``, so the host registers a **context provider** that performs the
re-fetch. Likewise, transcript persistence is the host's concern: register a
**transcript handler** to receive every finalized turn (e.g. to write it to
your datastore); the kit itself only streams turns to the browser over the
WebRTC data channel.

Register both ONCE, in the module the runtime container imports (see
docs/01-integration-guide.md)::

    # my_voice_app.py
    from voice_kit import set_context_provider, set_transcript_handler
    from voice_kit.runtime import app  # noqa: F401  (re-export for uvicorn)

    set_context_provider(my_provider)
    set_transcript_handler(my_sink)

Both callables run on the pipeline's event loop (the WebRTC packet pump), so
they MUST NOT block: wrap sync I/O (e.g. boto3) in ``asyncio.to_thread``, as
the source implementation did for its DynamoDB writes.
"""

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from .config import settings
from .types import TranscriptMessage, VoiceConfig

logger = logging.getLogger(__name__)


@dataclass
class SessionContext:
    """Everything the pipeline needs to run one session, resolved by session id."""

    system_prompt: str
    voice: VoiceConfig
    # Prior transcript, seeded into the LLM history so the agent keeps context
    # across reconnects (the browser retries with a fresh runtime_session_id but
    # the same session_id).
    initial_history: List[TranscriptMessage] = field(default_factory=list)
    # None → settings.session_time_limit_minutes * 60.
    time_limit_seconds: Optional[int] = None
    # Pipeline self-termination backstop for THIS session; None →
    # settings.idle_timeout_secs. A host whose own clock outlives the kit's
    # default raises it here: the idle timeout cancels the pipeline and closes
    # the peer connection, so one that lands inside a live session reads to the
    # browser as a dropped connection.
    idle_timeout_seconds: Optional[int] = None
    # Free-form extras for the host's own use (ignored by the kit).
    metadata: dict = field(default_factory=dict)


# (session_id) -> SessionContext. Async because the re-fetch is usually I/O.
ContextProvider = Callable[[str], Awaitable[SessionContext]]

# (session_id, message) -> None. Called once per finalized turn (user and
# assistant), in order. A failure is logged and swallowed — it must never kill
# the conversation turn.
TranscriptHandler = Callable[[str, TranscriptMessage], Awaitable[None]]


@dataclass
class ProcessorFactoryArgs:
    """Everything a host needs to assemble its own processor chain for a session.

    Handed to a registered :data:`ProcessorFactory` in place of the kit's default
    ``build_processors`` call. ``session_context`` is the host's own object back
    (including ``metadata``), so the factory can reach domain state it stashed
    during the re-fetch without a second lookup.
    """

    session_id: str
    session_context: SessionContext
    system_prompt: str
    voice: VoiceConfig
    initial_history: List[TranscriptMessage]
    emit: Optional[Callable[[str], None]] = None
    on_transcript_message: Optional[TranscriptHandler] = None


# (args) -> ordered list of pipecat FrameProcessors, spliced between the
# transport's input/VAD stages and its output. Fully replaces the default chain.
ProcessorFactory = Callable[["ProcessorFactoryArgs"], list]

# (session_id, pipeline_context, transport, emit) -> None. Awaited once per
# session right after the pipeline task is launched, so the host can start
# per-session work (timers, reapers) with the live pipeline in hand.
# ``PipelineContext`` is a STRING annotation on purpose: importing
# voice_kit.pipeline here would drag pipecat into the pipecat-free control
# plane. Do not "fix" it into a real import.
SessionStartHook = Callable[
    [str, "PipelineContext", object, Optional[Callable[[str], None]]],  # noqa: F821
    Awaitable[None],
]

# (session_id) -> None. Awaited by ``voice_kit.runtime.end_session`` after the
# pipeline task is cancelled — the host's teardown of whatever the start hook
# began. NOT fired when a reconnect supersedes a pipeline.
SessionEndHook = Callable[[str], Awaitable[None]]


async def default_context_provider(session_id: str) -> SessionContext:
    """Build a static context from settings alone (no per-session re-fetch).

    Suitable for single-persona agents: the system prompt comes from
    ``SYSTEM_PROMPT`` (or the contents of ``SYSTEM_PROMPT_PATH``) and the voice
    from ``TTS_PROVIDER`` / ``TTS_VOICE`` / ``TTS_MODEL``. Hosts with
    per-session personas, stored history, or per-session voices register their
    own provider instead.
    """
    prompt = settings.system_prompt
    if not prompt and settings.system_prompt_path:
        with open(settings.system_prompt_path, encoding="utf-8") as f:
            prompt = f.read()
    if not prompt:
        raise ValueError(
            "No system prompt configured: set SYSTEM_PROMPT / SYSTEM_PROMPT_PATH "
            "or register a context provider (voice_kit.set_context_provider)"
        )
    return SessionContext(
        system_prompt=prompt,
        voice=VoiceConfig(
            provider=settings.tts_provider,
            voice=settings.tts_voice,
            model=settings.tts_model,
        ),
    )


_context_provider: ContextProvider = default_context_provider
_transcript_handler: Optional[TranscriptHandler] = None
_processor_factory: Optional[ProcessorFactory] = None
_session_start_hook: Optional[SessionStartHook] = None
_session_end_hook: Optional[SessionEndHook] = None


def set_context_provider(provider: ContextProvider) -> None:
    """Register the host's session-context re-fetch (replaces the default)."""
    global _context_provider
    _context_provider = provider


def get_context_provider() -> ContextProvider:
    return _context_provider


def set_transcript_handler(handler: Optional[TranscriptHandler]) -> None:
    """Register the host's per-turn transcript sink (default: none)."""
    global _transcript_handler
    _transcript_handler = handler


def get_transcript_handler() -> Optional[TranscriptHandler]:
    return _transcript_handler


def set_processor_factory(factory: Optional[ProcessorFactory]) -> None:
    """Register a host chain builder (default: none → the kit's default chain)."""
    global _processor_factory
    _processor_factory = factory


def get_processor_factory() -> Optional[ProcessorFactory]:
    return _processor_factory


def set_session_start_hook(hook: Optional[SessionStartHook]) -> None:
    """Register per-session startup work run once the pipeline is launched."""
    global _session_start_hook
    _session_start_hook = hook


def get_session_start_hook() -> Optional[SessionStartHook]:
    return _session_start_hook


def set_session_end_hook(hook: Optional[SessionEndHook]) -> None:
    """Register per-session teardown run when the session is explicitly ended."""
    global _session_end_hook
    _session_end_hook = hook


def get_session_end_hook() -> Optional[SessionEndHook]:
    return _session_end_hook
