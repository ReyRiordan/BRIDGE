"""
Pipecat pipeline construction for one voice session.

``build_pipeline_for_session`` is the runtime's per-session factory. Following
the "pointer + re-fetch" pattern, the invoke payload carries only a
``session_id``; this module resolves everything else through the host's
registered context provider (``voice_kit.context``) and assembles the
processor chain:

    transport input → VADProcessor (Silero)
        → STTProcessor → LLMProcessor → TTSProcessor → TranscriptSinkProcessor
        → transport output

The session time limit (``settings.session_time_limit_minutes``, overridable
per session via ``SessionContext.time_limit_seconds``) is applied as the
``PipelineWorker`` idle timeout so the runtime self-terminates at the limit.
An AgentCore ``maxLifetime`` backstop is configured separately in infra.

Pipecat APIs verified against pipecat-ai 1.3.0: VAD is a standalone
``VADProcessor`` in the pipeline (it is no longer a ``TransportParams`` field);
``PipelineTask`` is a deprecated alias for ``PipelineWorker`` and the
self-termination knob is ``idle_timeout_secs``.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from .config import settings
from .context import SessionContext, get_context_provider, get_transcript_handler
from .processors import (
    LLMProcessor,
    STTProcessor,
    TranscriptSinkProcessor,
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
    time_limit_seconds: int
    pipeline: object
    task: object


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
        TranscriptSinkProcessor(
            session_id=session_id,
            emit=emit,
            on_transcript_message=on_transcript_message,
        ),
    ]


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
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.audio.vad_processor import VADProcessor

    context = await load_session_context(session_id)
    time_limit_seconds = (
        context.time_limit_seconds
        if context.time_limit_seconds is not None
        else settings.session_time_limit_minutes * 60
    )

    processors = build_processors(
        system_prompt=context.system_prompt,
        session_id=session_id,
        voice=context.voice,
        initial_history=context.initial_history,
        emit=emit,
        on_transcript_message=get_transcript_handler(),
    )

    # transport input → VADProcessor → STT → LLM → TTS → sink → output.
    # VADProcessor (Silero) emits the VADUserStarted/VADUserStopped frames the STT
    # processor gates on; without it nothing would trigger transcription.
    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(vad_analyzer=SileroVADAnalyzer()),
            *processors,
            transport.output(),
        ]
    )

    # PipelineWorker self-terminates after `idle_timeout_secs` of no speaking
    # activity, capping the conversation at the configured limit.
    # PipelineParams carries no timeout field.
    task = PipelineWorker(
        pipeline,
        params=PipelineParams(),
        idle_timeout_secs=time_limit_seconds,
    )

    return PipelineContext(
        session_id=session_id,
        voice=context.voice,
        system_prompt=context.system_prompt,
        time_limit_seconds=time_limit_seconds,
        pipeline=pipeline,
        task=task,
    )
