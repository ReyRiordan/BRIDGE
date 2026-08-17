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
    # Free-form extras for the host's own use (ignored by the kit).
    metadata: dict = field(default_factory=dict)


# (session_id) -> SessionContext. Async because the re-fetch is usually I/O.
ContextProvider = Callable[[str], Awaitable[SessionContext]]

# (session_id, message) -> None. Called once per finalized turn (user and
# assistant), in order. A failure is logged and swallowed — it must never kill
# the conversation turn.
TranscriptHandler = Callable[[str, TranscriptMessage], Awaitable[None]]


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
