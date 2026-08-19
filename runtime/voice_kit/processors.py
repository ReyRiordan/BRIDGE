"""
Pipecat ``FrameProcessor`` wrappers for the voice conversation pipeline.

These processors wrap the provider factories (``voice_kit/providers/*``) and
adapt their batch APIs to Pipecat's frame-streaming model.

Pipeline data flow (one user turn):

    InputAudioRawFrame ────┐
    VADUserStartedSpeaking │  STTProcessor buffers audio between the VAD start/stop
    VADUserStoppedSpeaking ┘  events (emitted by Pipecat's VADProcessor upstream),
                              transcribes the utterance, and emits a
                              TranscriptMessageFrame(role="user").
                                  │
    LLMProcessor appends the user turn to its running history, calls the
    LLM, and emits TranscriptMessageFrame(role="assistant"). It passes
    the user frame through unchanged so downstream stages still see it.
    An optional turn gate can suppress the LLM call for a turn (the user
    frame still flows), and an optional turn context injects an ephemeral
    system message into the call only.
                                  │
    TTSProcessor synthesizes the assistant text to audio frames (running the
    sync streaming generator in a thread) and passes the transcript frame
    through.
                                  │
    EventSinkProcessor hands every TranscriptMessageFrame to the host's
    registered transcript handler (voice_kit.context) and emits it over the
    WebRTC data channel — as raw transcript JSON by default, or as whatever a
    host-supplied mapper returns.

Because each TranscriptMessageFrame is carried end-to-end (not consumed by the
stage that produced it), the sink sits last and sees both the user and
assistant messages of every turn, in order.

Frame names verified against pipecat-ai 1.3.0: the upstream ``VADProcessor``
emits ``VADUserStartedSpeakingFrame`` / ``VADUserStoppedSpeakingFrame`` (distinct
``SystemFrame`` subclasses, NOT the plain ``User*SpeakingFrame``), and the output
transport only renders ``TTSAudioRawFrame`` (an ``OutputAudioRawFrame``), so the
TTS stage emits that rather than a bare ``AudioRawFrame``.
"""

import asyncio
import collections
import logging
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

# Voice-only dependency — installed by requirements-voice.txt, imported only in
# the voice container.
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .config import ReasoningEffort
from .context import TranscriptHandler
from .providers.llm import get_llm_model
from .providers.stt import get_stt_model
from .providers.tts import get_tts_model
from .types import TranscriptMessage, VoiceConfig

logger = logging.getLogger(__name__)


class TranscriptMessageFrame(Frame):
    """A finalized conversation turn (user or assistant) flowing through the pipeline.

    Carries a :class:`TranscriptMessage` so downstream stages (LLM history, TTS,
    the transcript sink) can act on the same object without re-deriving
    role/content.
    """

    def __init__(self, message: TranscriptMessage):
        super().__init__()
        self.message = message


class STTProcessor(FrameProcessor):
    """Buffer user audio between VAD start/stop, then batch-transcribe.

    Pipecat's ``VADProcessor`` (Silero) upstream emits
    ``VADUserStartedSpeakingFrame`` / ``VADUserStoppedSpeakingFrame``; this
    processor accumulates the ``InputAudioRawFrame``s in between and, on stop,
    calls ``transcribe_stream()`` then emits a user transcript frame.

    Silero only emits ``VADUserStartedSpeakingFrame`` after a sustained
    confirmation window (``start_secs``), so the utterance onset (first
    syllable/word) arrives *before* capture begins. To avoid dropping it, the
    processor continuously retains the last ``preroll_ms`` of pre-speech audio
    in a byte-bounded ring buffer and seeds the utterance buffer with it on VAD
    start. (Do NOT instead lower Silero's start_secs/confidence — that trades
    dropped onsets for phantom turns.)
    """

    def __init__(self, provider: str, sample_rate: int = 16000, preroll_ms: int = 300):
        super().__init__()
        self._provider = provider
        self._sample_rate = sample_rate
        self._buffer: List[bytes] = []
        self._capturing = False
        # Ring buffer of pre-speech audio, bounded by accumulated bytes (not
        # frame count, since frame sizes vary). 2 = int16 bytes per sample.
        self._preroll_bytes = int(preroll_ms * sample_rate * 2 / 1000)
        self._preroll: "collections.deque[bytes]" = collections.deque()
        self._preroll_total = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._capturing = True
            # Seed the utterance with retained pre-speech audio so the onset
            # (dropped during Silero's start_secs window) is transcribed.
            self._buffer = list(self._preroll)
            self._preroll.clear()
            self._preroll_total = 0
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            if self._capturing:
                self._buffer.append(frame.audio)
                # Don't forward raw mic audio downstream; STT consumes it.
                return
            # Not capturing: retain in the byte-bounded pre-roll ring buffer,
            # evicting the oldest chunks once over budget. Not forwarded.
            self._preroll.append(frame.audio)
            self._preroll_total += len(frame.audio)
            while self._preroll_total > self._preroll_bytes and self._preroll:
                self._preroll_total -= len(self._preroll.popleft())
            return

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._capturing = False
            await self.push_frame(frame, direction)
            await self._flush()
            return

        await self.push_frame(frame, direction)

    async def _flush(self) -> None:
        """Transcribe the buffered utterance and push a user transcript frame."""
        if not self._buffer:
            return

        import numpy as np

        pcm = b"".join(self._buffer)
        self._buffer = []
        arr = np.frombuffer(pcm, dtype=np.int16)
        if arr.size == 0:
            return

        stt = get_stt_model(self._provider)
        started = time.perf_counter()
        text = await stt.transcribe_stream((self._sample_rate, arr))
        logger.info(
            "[timing] ASR (%s): %.2fs", self._provider, time.perf_counter() - started
        )
        if not text or not text.strip():
            return

        message = TranscriptMessage(
            role="user", content=text.strip(), timestamp=datetime.utcnow()
        )
        await self.push_frame(
            TranscriptMessageFrame(message), FrameDirection.DOWNSTREAM
        )


class LLMProcessor(FrameProcessor):
    """Generate the assistant reply for each user turn via the LLM.

    Maintains running conversation history (seeded with any prior transcript so
    the agent keeps context across reconnects) and emits an assistant
    transcript frame. User frames are passed through unchanged.

    Two optional host seams:

    - ``turn_gate()`` — consulted after the user frame is forwarded and appended
      to history. Returning False skips the LLM call for this turn (no assistant
      frame, no audio), e.g. because the host already ended the conversation.
      History stays complete either way, so a reconnect resumes correctly.
    - ``turn_context()`` — a per-turn string injected as an ephemeral system
      message **immediately before the final user message**, never stored in
      history. The position is deliberate: ``system_prompt`` + history stays a
      stable, cacheable prefix while the marker keeps maximal recency. (The
      legacy app instead prefixed it ahead of the whole history.)
    """

    def __init__(
        self,
        system_prompt: str,
        provider: str,
        model: str,
        reasoning_effort: ReasoningEffort = "none",
        providers: Optional[List[str]] = None,
        initial_history: Optional[List[TranscriptMessage]] = None,
        turn_gate: Optional[Callable[[], bool]] = None,
        turn_context: Optional[Callable[[], Optional[str]]] = None,
    ):
        super().__init__()
        self._system_prompt = system_prompt
        self._provider = provider
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._providers = providers
        self._history: List[TranscriptMessage] = list(initial_history or [])
        self._turn_gate = turn_gate
        self._turn_context = turn_context

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not isinstance(frame, TranscriptMessageFrame):
            await self.push_frame(frame, direction)
            return

        # Pass the user turn through so the transcript sink still records it.
        await self.push_frame(frame, direction)

        if frame.message.role != "user":
            return

        # Append BEFORE the gate check: a gated turn is still part of the
        # conversation the next connection resumes from.
        self._history.append(frame.message)

        if self._turn_gate is not None and not self._turn_gate():
            logger.info("Turn gated: skipping LLM call")
            return

        llm = get_llm_model(
            provider=self._provider,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            providers=self._providers,
        )
        # Roles are already chat-API-native (user/assistant) — no mapping needed.
        messages: List[Dict] = [
            {"role": m.role, "content": m.content} for m in self._history
        ]
        if self._turn_context is not None:
            turn_context = self._turn_context()
            if turn_context:
                # Immediately before the final user message (see class docstring).
                messages.insert(
                    len(messages) - 1, {"role": "system", "content": turn_context}
                )
        started = time.perf_counter()
        response_text = await llm.chat(messages, self._system_prompt)
        logger.info(
            "[timing] patient-LLM (%s): %.2fs",
            self._model,
            time.perf_counter() - started,
        )

        assistant_msg = TranscriptMessage(
            role="assistant", content=response_text, timestamp=datetime.utcnow()
        )
        self._history.append(assistant_msg)
        await self.push_frame(
            TranscriptMessageFrame(assistant_msg), FrameDirection.DOWNSTREAM
        )


class TTSProcessor(FrameProcessor):
    """Synthesize assistant text to audio frames using the session's TTS voice.

    Runs the synchronous streaming generator in a worker thread (so it never
    blocks the event loop / WebRTC packet pump) and pushes each chunk as a
    ``TTSAudioRawFrame``. Transcript frames are passed through unchanged.
    """

    def __init__(self, voice: VoiceConfig):
        super().__init__()
        self._speech = voice

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # Always forward the frame (the transcript sink is downstream of TTS).
        await self.push_frame(frame, direction)

        if not isinstance(frame, TranscriptMessageFrame):
            return
        if frame.message.role != "assistant":
            return

        tts = get_tts_model(
            provider=self._speech.provider,
            voice=self._speech.voice,
            model=self._speech.model,
            speed=self._speech.speed,
        )
        started = time.perf_counter()
        chunks = await asyncio.to_thread(
            list, tts.stream_tts_sync(frame.message.content)
        )
        logger.info(
            "[timing] TTS (%s): %.2fs",
            self._speech.provider,
            time.perf_counter() - started,
        )
        for sample_rate, audio in chunks:
            # TTSAudioRawFrame is an OutputAudioRawFrame — the output transport
            # only renders output-typed audio frames (a bare AudioRawFrame is
            # silently dropped on the output side).
            await self.push_frame(
                TTSAudioRawFrame(
                    audio=_to_pcm16_bytes(audio),
                    sample_rate=sample_rate,
                    num_channels=1,
                ),
                FrameDirection.DOWNSTREAM,
            )


class EventSinkProcessor(FrameProcessor):
    """Hand each finalized turn to the host's sink and emit it over the data channel.

    For every :class:`TranscriptMessageFrame` it (1) awaits the host-registered
    transcript handler (``voice_kit.context.set_transcript_handler``) — the
    pluggable replacement for server-side persistence; a SYNC sink (e.g. a
    boto3 PutItem) must be wrapped by the host in ``asyncio.to_thread`` so the
    Pipecat event loop (the WebRTC packet pump) is never blocked — and (2)
    emits a JSON string over the WebRTC data channel so the frontend renders
    the live transcript. Both are failure-isolated: neither a sink error nor an
    emit error may kill the conversation turn.

    What goes on the wire is the host's choice: ``transcript_event`` maps a
    message to the JSON string to send, and returning ``None`` sends nothing
    (e.g. a host whose own layer already emitted that role). Without a mapper
    the raw ``TranscriptMessage`` JSON is sent, as before.
    """

    def __init__(
        self,
        session_id: str,
        emit: Optional[Callable[[str], None]] = None,
        on_transcript_message: Optional[TranscriptHandler] = None,
        transcript_event: Optional[Callable[[TranscriptMessage], Optional[str]]] = None,
    ):
        super().__init__()
        self._session_id = session_id
        self._emit = emit
        self._on_transcript_message = on_transcript_message
        self._transcript_event = transcript_event

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptMessageFrame):
            if self._on_transcript_message is not None:
                try:
                    await self._on_transcript_message(self._session_id, frame.message)
                except Exception as e:  # a sink failure must never kill the turn
                    logger.warning(
                        "[%s] transcript handler failed: %s", self._session_id, e
                    )
            if self._emit is not None:
                try:
                    if self._transcript_event is not None:
                        payload = self._transcript_event(frame.message)
                    else:
                        payload = frame.message.model_dump_json()
                    if payload is not None:
                        self._emit(payload)
                except Exception as e:  # data-channel emit must never kill the turn
                    logger.warning(
                        "[%s] data-channel emit failed: %s", self._session_id, e
                    )

        await self.push_frame(frame, direction)


def _to_pcm16_bytes(audio) -> bytes:
    """Convert a TTS audio chunk (float32 or int16 ndarray) to int16 PCM bytes."""
    import numpy as np

    arr = np.asarray(audio)
    if arr.dtype != np.int16:
        arr = np.clip(arr, -1.0, 1.0)
        arr = (arr * 32767.0).astype(np.int16)
    return arr.tobytes()
