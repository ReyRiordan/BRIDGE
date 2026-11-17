"""
The BRIDGE voice runtime — the uvicorn/Docker target (``bridge.app:app``).

A thin wiring module: it registers the game engine on the kit's extension points
at import time and re-exports ``voice_kit.runtime:app``. All game logic lives in
the sibling modules; nothing here knows how a turn is scored.

The chain it builds per session::

    transport.input() -> VAD
        -> STTProcessor -> RefereeProcessor -> LLMProcessor(patient)
        -> TTSProcessor -> EventSinkProcessor
        -> transport.output()

Event ordering on the data channel, per turn, is guaranteed by construction:
``transcript_update{student}`` -> ``action_detected``xN -> ``state_update`` ->
(``game_over``?) -> ``transcript_update{patient}``. The referee's events are sent
directly from ``score_turn``, which completes before the frame reaches the
patient LLM; the patient's transcript is the only one travelling the frame path.
``timer`` ticks interleave freely.
"""

import logging

from voice_kit import (
    SessionContext,
    TranscriptMessage,
    set_context_provider,
    set_processor_factory,
    set_session_end_hook,
    set_session_start_hook,
    set_transcript_handler,
)
from voice_kit.config import settings
from voice_kit.processors import (
    EventSinkProcessor,
    LLMProcessor,
    STTProcessor,
    TTSProcessor,
)

# `app` is re-exported: it is the uvicorn target named by Dockerfile.voice.
from voice_kit.runtime import app, end_session  # noqa: F401

from .config import (
    REFEREE_TIMEOUT_SECONDS,
    idle_timeout_for,
    load_referee_prompt,
    load_scenario,
    parse_session_id,
)
from .emitter import GameEvents, transcript_event
from .patient import build_patient_prompt, build_voice_config, turn_context
from .recorder import SessionRecorder
from .referee import RefereeProcessor
from .session import STATUS_ACTIVE, drop_session, get_or_create_session, get_session
from .store import get_session_table
from .timer import (
    cancel_reaper,
    cancel_timer,
    set_pipeline_canceller,
    start_reaper,
    start_timer,
)

logger = logging.getLogger(__name__)


async def provide_context(session_id: str) -> SessionContext:
    """Resolve (or resume) the game session behind a session id.

    Pointer + re-fetch, with the container's own memory as the store: a reconnect
    onto the same warm container gets the same :class:`GameSession` back, so
    escalation, action states, clock origin and transcript all survive the
    pipeline rebuild. A reconnect that lands on a different container starts
    fresh — accepted, since the session record is write-only and never read
    back to resume a game (see 05-gotchas.md).

    The scenario is the session id's ``<scenario_id>--`` prefix; a missing or
    unknown one raises rather than falling back to some default scenario.
    """
    scenario = load_scenario(parse_session_id(session_id))
    session = get_or_create_session(session_id, scenario)
    # A reconnect keeps the recorder it already has (and its queue).
    if session.recorder is None:
        session.recorder = SessionRecorder(session_id, get_session_table())
    return SessionContext(
        system_prompt=build_patient_prompt(scenario),
        voice=build_voice_config(scenario),
        # Seeds the patient LLM's history so it resumes mid-conversation.
        initial_history=list(session.transcript),
        time_limit_seconds=scenario["time_limit"],
        # The game clock outlives the kit's default backstop, and pipecat's idle
        # timeout cancels the pipeline outright: left at the default, a silent
        # run to the time limit loses its data channel before `game_over` is
        # emitted and the student is shown a lost connection instead of the
        # timeout debrief. Derived from the scenario so it tracks any edit to it.
        idle_timeout_seconds=idle_timeout_for(scenario),
        metadata={"game": session},
    )


def build_game_processors(args) -> list:
    """The BRIDGE chain: the kit's stages with the referee spliced after STT."""
    session = args.session_context.metadata["game"]
    events = GameEvents(args.session_id, args.emit)
    return [
        STTProcessor(
            provider=settings.stt_provider, preroll_ms=settings.stt_preroll_ms
        ),
        RefereeProcessor(
            session=session,
            events=events,
            system_prompt=load_referee_prompt(),
            timeout_seconds=REFEREE_TIMEOUT_SECONDS,
            on_game_over=lambda: start_reaper(session.session_id),
        ),
        LLMProcessor(
            system_prompt=args.system_prompt,
            provider=settings.llm_provider,
            model=settings.llm_model,
            reasoning_effort=settings.llm_reasoning,
            providers=settings.llm_providers,
            initial_history=args.initial_history,
            # A finished game still transcribes the student, but the patient
            # says nothing more.
            turn_gate=lambda: session.status == "active",
            turn_context=lambda: turn_context(session),
        ),
        TTSProcessor(voice=args.voice),
        EventSinkProcessor(
            session_id=args.session_id,
            emit=args.emit,
            on_transcript_message=args.on_transcript_message,
            # Patient turns only — the referee already emitted the student's.
            transcript_event=transcript_event,
        ),
    ]


async def store_turn(session_id: str, message: TranscriptMessage) -> None:
    """Append every finalized turn to the in-memory session transcript.

    The patient's turns are recorded here; the student's are recorded by the
    referee, which knows what each one scored.
    """
    session = get_session(session_id)
    if session is None:
        return
    session.transcript.append(message)
    if message.role == "assistant" and session.recorder:
        session.recorder.patient_turn(session, message)


async def on_session_start(session_id, pipeline_context, transport, emit) -> None:
    """Start the clock and publish the authoritative state for this connection."""
    session = pipeline_context.game
    events = GameEvents(session_id, emit)
    # Once per GameSession: the recorder ignores a reconnect's second call.
    if session.recorder:
        session.recorder.started(session)
    # Right after the pipeline is built, so a cold start never eats the clock.
    start_timer(session, events, on_expire=lambda: start_reaper(session_id))

    # The data channel is NOT open yet inside the connection callback, so the
    # connect-time state_update has to wait for the transport's own event
    # (emitting it here would be swallowed by the emitter and silently lost).
    @transport.event_handler("on_client_connected")
    async def _on_client_connected(_transport, _client):
        events.state(session)


async def on_session_end(session_id: str) -> None:
    """Tear the game down: stop the clock, disarm the reaper, forget the session.

    A session still active here was abandoned (the student left mid-game). The
    record is marked so, then its queue gets a bounded drain.
    """
    cancel_timer(session_id)
    cancel_reaper(session_id)
    session = drop_session(session_id)
    if session is not None and session.recorder is not None:
        if session.status == STATUS_ACTIVE:
            session.recorder.abandoned(session)
        await session.recorder.close()
    logger.info("[%s] game session torn down", session_id)


set_context_provider(provide_context)
set_transcript_handler(store_turn)
set_processor_factory(build_game_processors)
set_session_start_hook(on_session_start)
set_session_end_hook(on_session_end)
# The reaper ends the session through the kit's own teardown path.
set_pipeline_canceller(end_session)
