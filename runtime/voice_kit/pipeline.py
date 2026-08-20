"""
Pipecat pipeline construction for one voice session.

``build_pipeline_for_session`` is the runtime's per-session factory. Following
the "pointer + re-fetch" pattern, the invoke payload carries only a
``session_id``; this module resolves everything else through the host's
registered context provider (``voice_kit.context``) and assembles the
processor chain:

    transport input → VADProcessor (Silero)
        → STTProcessor → LLMProcessor → TTSProcessor → EventSinkProcessor
        → transport output

A host that needs its own stages registers a **processor factory**
(``voice_kit.set_processor_factory``); ``resolve_processors`` dispatches to it
and falls back to the default chain above.

``PipelineWorker``'s idle timeout comes from ``settings.idle_timeout_secs`` —
the runtime's self-termination backstop for abandoned containers, deliberately
independent of any application time limit. ``SessionContext.time_limit_seconds``
is carried on the :class:`PipelineContext` for the host's reporting only; the
kit never enforces it. An AgentCore ``maxLifetime`` backstop is configured
separately in infra.

The kit still never enforces the app's limit, but a host whose clock outlives
the default backstop must raise it per session through
``SessionContext.idle_timeout_seconds``: the idle timeout *cancels* the pipeline
and closes the peer connection, so one that fires inside a live session is
indistinguishable from a dropped connection in the browser.

Pipecat APIs verified against pipecat-ai 1.3.0: VAD is a standalone
``VADProcessor`` in the pipeline (it is no longer a ``TransportParams`` field);
``PipelineTask`` is a deprecated alias for ``PipelineWorker`` and the
self-termination knob is ``idle_timeout_secs``.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .config import settings
from .context import (
    ProcessorFactoryArgs,
    SessionContext,
    get_context_provider,
    get_processor_factory,
    get_transcript_handler,
)
from .processors import (
    EventSinkProcessor,
    LLMProcessor,
    STTProcessor,
    TTSProcessor,
)
from .types import TranscriptMessage, VoiceConfig

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """Everything the runtime needs to run one session's pipeline.

    Returned alongside the assembled pipeline so the worker can drive the task
    (e.g. start/cancel) and key its peer-connection bookkeeping.
    """

    session_id: str
    voice: VoiceConfig
    system_prompt: str
    # Informational: the host's own conversation cap, never enforced by the kit
    # (self-termination is driven by idle_timeout_seconds below).
    time_limit_seconds: int
    # The resolved pipeline idle timeout for this session: the host's
    # SessionContext override, else settings.idle_timeout_secs.
    idle_timeout_seconds: int
    pipeline: object
    task: object
    # The host's SessionContext.metadata, forwarded verbatim, plus the object it
    # stored under "game" hoisted to a field so session hooks can reach the
    # host's per-session state without digging.
    metadata: dict = field(default_factory=dict)
    game: object = None


async def load_session_context(session_id: str) -> SessionContext:
    """Resolve the session's context through the host's registered provider.

    The provider performs whatever re-fetch the host needs (pointer + re-fetch);
    the default provider builds a static context from settings alone. A
    provider doing sync I/O must wrap it in ``asyncio.to_thread`` — this runs
    on the pipeline's event loop.
    """
    context = await get_context_provider()(session_id)
    logger.info(
        "Loaded context for session %s: voice=%s/%s, prior_turns=%d",
        session_id,
        context.voice.provider,
        context.voice.voice,
        len(context.initial_history),
    )
    return context


def build_processors(
    *,
    system_prompt: str,
    session_id: str,
    voice: VoiceConfig,
    initial_history: Optional[List[TranscriptMessage]] = None,
    emit=None,
    on_transcript_message=None,
) -> list:
    """Build the ordered STT → LLM → TTS → transcript-sink processor chain.

    Providers/models come from settings so the whole pipeline is env-switchable.
    """
    return [
        STTProcessor(
            provider=settings.stt_provider, preroll_ms=settings.stt_preroll_ms
        ),
        LLMProcessor(
            system_prompt=system_prompt,
            provider=settings.llm_provider,
            model=settings.llm_model,
            reasoning_effort=settings.llm_reasoning,
            providers=settings.llm_providers,
            initial_history=initial_history,
        ),
        TTSProcessor(voice=voice),
        EventSinkProcessor(
            session_id=session_id,
            emit=emit,
            on_transcript_message=on_transcript_message,
        ),
    ]


def resolve_processors(
    session_id: str,
    context: SessionContext,
    emit=None,
    on_transcript_message=None,
) -> list:
    """Return the processor chain for this session: host factory, else default.

    Split out of ``build_pipeline_for_session`` so the dispatch is unit-testable
    without pipecat (the default branch still imports it, a registered factory
    need not).
    """
    factory = get_processor_factory()
    if factory is None:
        return build_processors(
            system_prompt=context.system_prompt,
            session_id=session_id,
            voice=context.voice,
            initial_history=context.initial_history,
            emit=emit,
            on_transcript_message=on_transcript_message,
        )
    logger.info("Building processors for session %s via host factory", session_id)
    return factory(
        ProcessorFactoryArgs(
            session_id=session_id,
            session_context=context,
            system_prompt=context.system_prompt,
            voice=context.voice,
            initial_history=context.initial_history,
            emit=emit,
            on_transcript_message=on_transcript_message,
        )
    )


async def build_pipeline_for_session(
    session_id: str,
    transport,
    emit=None,
) -> PipelineContext:
    """Assemble the full Pipecat pipeline for a session.

    Args:
        session_id: The host's session id (the invoke payload's only context
            pointer, resolved via the registered context provider).
        transport: The AgentCore/aiortc transport providing input/output frame
            processors and the data channel.
        emit: Optional callback that sends a JSON string over the data channel;
            wired into the transcript sink for the live transcript.

    Returns:
        A :class:`PipelineContext` holding the voice, prompt, derived time
        limit, and the assembled pipeline + task.
    """
    # Pipecat construction lives behind lazy imports so this module can be
    # imported (e.g. under stubs) without the voice dependencies installed.
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.audio.vad_processor import VADProcessor

    context = await load_session_context(session_id)
    time_limit_seconds = (
        context.time_limit_seconds
        if context.time_limit_seconds is not None
        else settings.session_time_limit_minutes * 60
    )
    idle_timeout_seconds = (
        context.idle_timeout_seconds
        if context.idle_timeout_seconds is not None
        else settings.idle_timeout_secs
    )

    processors = resolve_processors(
        session_id,
        context,
        emit=emit,
        on_transcript_message=get_transcript_handler(),
    )

    # transport input → VADProcessor → STT → LLM → TTS → sink → output.
    # VADProcessor (Silero) emits the VADUserStarted/VADUserStopped frames the STT
    # processor gates on; without it nothing would trigger transcription.
    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        confidence=settings.vad_confidence,
                        start_secs=settings.vad_start_secs,
                        stop_secs=settings.vad_stop_secs,
                        min_volume=settings.vad_min_volume,
                    )
                ),
                speech_activity_period=settings.vad_speech_activity_period,
                audio_idle_timeout=settings.vad_audio_idle_timeout,
            ),
            *processors,
            transport.output(),
        ]
    )

    # PipelineWorker self-terminates after `idle_timeout_seconds` of no speaking
    # activity — a backstop against abandoned containers, NOT the app's time
    # limit (a host may cap a conversation far above or below it). A host that
    # caps it ABOVE must raise this through SessionContext.idle_timeout_seconds,
    # or the pipeline dies mid-session and the browser sees a dropped call.
    # PipelineParams carries no timeout field.
    logger.info(
        "Session %s: time limit %ss, pipeline idle timeout %ss",
        session_id,
        time_limit_seconds,
        idle_timeout_seconds,
    )
    task = PipelineWorker(
        pipeline,
        params=PipelineParams(),
        idle_timeout_secs=idle_timeout_seconds,
    )

    return PipelineContext(
        session_id=session_id,
        voice=context.voice,
        system_prompt=context.system_prompt,
        time_limit_seconds=time_limit_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        pipeline=pipeline,
        task=task,
        metadata=context.metadata,
        game=context.metadata.get("game"),
    )
