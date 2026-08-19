"""
The game-engine seams on the pipeline side: processor-factory dispatch, the
session hook registry, and the metadata/game passthrough on PipelineContext.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit import (  # noqa: E402
    ProcessorFactoryArgs,
    SessionContext,
    TranscriptMessage,
    VoiceConfig,
    get_processor_factory,
    get_session_end_hook,
    get_session_start_hook,
    set_processor_factory,
    set_session_end_hook,
    set_session_start_hook,
)
from voice_kit.context import default_context_provider  # noqa: E402
from voice_kit.pipeline import (  # noqa: E402
    PipelineContext,
    build_pipeline_for_session,
    resolve_processors,
)
from voice_kit.processors import (  # noqa: E402
    EventSinkProcessor,
    LLMProcessor,
    STTProcessor,
    TTSProcessor,
)


def _clear_hooks() -> None:
    set_processor_factory(None)
    set_session_start_hook(None)
    set_session_end_hook(None)


@pytest.fixture(autouse=True)
def clean_registry():
    """Module-global hooks are process-wide — always restore the empty default.

    Cleared on the way IN as well: importing a host wiring module (e.g.
    ``bridge.app``) registers its hooks at import time, so another test file in
    the same session can leave them set before this one runs.
    """
    _clear_hooks()
    yield
    _clear_hooks()


def a_context(**overrides) -> SessionContext:
    kwargs = dict(
        system_prompt="be helpful",
        voice=VoiceConfig(provider="polly", voice="Ruth"),
        initial_history=[
            TranscriptMessage(
                role="user", content="hello", timestamp=datetime(2026, 1, 1)
            )
        ],
    )
    kwargs.update(overrides)
    return SessionContext(**kwargs)


def test_no_factory_returns_the_default_chain():
    processors = resolve_processors("sess-1", a_context())

    assert [type(p) for p in processors] == [
        STTProcessor,
        LLMProcessor,
        TTSProcessor,
        EventSinkProcessor,
    ]


def test_registered_factory_replaces_the_chain_and_receives_full_args():
    seen = {}

    def factory(args: ProcessorFactoryArgs) -> list:
        seen["args"] = args
        return ["only-my-processor"]

    set_processor_factory(factory)

    def emit(_json: str) -> None: ...

    async def on_transcript(_session_id, _message): ...

    context = a_context(metadata={"game": object()})
    processors = resolve_processors(
        "sess-1", context, emit=emit, on_transcript_message=on_transcript
    )

    assert processors == ["only-my-processor"]
    args = seen["args"]
    assert args.session_id == "sess-1"
    # The host's own object back — including the metadata it stashed.
    assert args.session_context is context
    assert args.system_prompt == "be helpful"
    assert args.voice == context.voice
    assert args.initial_history == context.initial_history
    assert args.emit is emit
    assert args.on_transcript_message is on_transcript


def test_processor_factory_registry_round_trips():
    assert get_processor_factory() is None

    def factory(args): ...

    set_processor_factory(factory)
    assert get_processor_factory() is factory


def test_session_hook_registries_round_trip():
    assert get_session_start_hook() is None
    assert get_session_end_hook() is None

    async def start(session_id, context, transport, emit): ...

    async def end(session_id): ...

    set_session_start_hook(start)
    set_session_end_hook(end)

    assert get_session_start_hook() is start
    assert get_session_end_hook() is end


def test_pipeline_context_carries_metadata_and_game():
    game = object()
    context = PipelineContext(
        session_id="sess-1",
        voice=VoiceConfig(provider="polly", voice="Ruth"),
        system_prompt="p",
        time_limit_seconds=600,
        pipeline=None,
        task=None,
        metadata={"game": game},
        game=game,
    )

    assert context.game is game
    assert context.metadata["game"] is game


def test_pipeline_context_defaults_are_empty():
    context = PipelineContext(
        session_id="sess-1",
        voice=VoiceConfig(provider="polly", voice="Ruth"),
        system_prompt="p",
        time_limit_seconds=600,
        pipeline=None,
        task=None,
    )

    assert context.metadata == {}
    assert context.game is None


def test_build_pipeline_uses_idle_timeout_and_hoists_game(monkeypatch):
    """End-to-end assembly with a fake transport: real pipecat, no I/O."""
    import asyncio

    from pipecat.processors.frame_processor import FrameProcessor
    from voice_kit import set_context_provider
    from voice_kit.config import settings

    game = object()

    async def provider(session_id: str) -> SessionContext:
        return a_context(time_limit_seconds=1800, metadata={"game": game})

    class FakeTransport:
        def input(self):
            return FrameProcessor()

        def output(self):
            return FrameProcessor()

    set_processor_factory(lambda args: [FrameProcessor()])
    set_context_provider(provider)
    monkeypatch.setattr(settings, "idle_timeout_secs", 42)
    try:
        context = asyncio.run(
            build_pipeline_for_session("sess-1", FakeTransport(), emit=None)
        )
    finally:
        set_context_provider(default_context_provider)

    assert context.game is game
    assert context.metadata == {"game": game}
    # The app's own limit is reported but does NOT drive self-termination.
    assert context.time_limit_seconds == 1800
    assert context.task._idle_timeout_secs == 42
