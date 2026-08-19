"""
Processor-level seams: the LLM turn gate + turn context, the event sink's
transcript mapper, and VoiceConfig.speed reaching the TTS factory.

Frames are fed straight into ``process_frame`` and captured via a stubbed
``push_frame`` — no pipeline, no transport, no network.
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit import TranscriptMessage, VoiceConfig  # noqa: E402
from voice_kit import processors as processors_module  # noqa: E402
from voice_kit.processors import (  # noqa: E402
    EventSinkProcessor,
    LLMProcessor,
    TranscriptMessageFrame,
    TTSProcessor,
)


def user_frame(content: str = "I need help") -> TranscriptMessageFrame:
    return TranscriptMessageFrame(
        TranscriptMessage(role="user", content=content, timestamp=datetime(2026, 1, 1))
    )


class StubLLM:
    """Records the messages of every chat() call; replies with a fixed line."""

    def __init__(self):
        self.calls = []

    async def chat(self, messages, system_prompt, response_format=None):
        self.calls.append({"messages": messages, "system_prompt": system_prompt})
        return "I hear you."


@pytest.fixture
def captured(monkeypatch):
    """Capture pushed frames on any processor built in the test."""
    frames = []

    async def fake_push(self, frame, direction=None):
        frames.append(frame)

    monkeypatch.setattr(
        processors_module.FrameProcessor, "push_frame", fake_push, raising=False
    )

    # The base process_frame does pipeline bookkeeping we don't have here.
    async def noop_process(self, frame, direction):
        return None

    monkeypatch.setattr(
        processors_module.FrameProcessor, "process_frame", noop_process, raising=False
    )
    return frames


@pytest.fixture
def stub_llm(monkeypatch):
    llm = StubLLM()
    monkeypatch.setattr(processors_module, "get_llm_model", lambda **kwargs: llm)
    return llm


def an_llm_processor(**overrides) -> LLMProcessor:
    kwargs = dict(
        system_prompt="you are a patient",
        provider="openrouter",
        model="anthropic/claude-haiku-4.5",
    )
    kwargs.update(overrides)
    return LLMProcessor(**kwargs)


def test_gated_turn_skips_the_llm_but_still_forwards_the_user_frame(captured, stub_llm):
    processor = an_llm_processor(turn_gate=lambda: False)
    frame = user_frame()

    asyncio.run(processor.process_frame(frame, None))

    # The transcript still reaches the sink; nothing else is produced.
    assert captured == [frame]
    assert stub_llm.calls == []
    # ...and history keeps the turn, so a reconnect resumes with it.
    assert [m.content for m in processor._history] == ["I need help"]


def test_open_gate_calls_the_llm_and_emits_the_assistant_frame(captured, stub_llm):
    processor = an_llm_processor(turn_gate=lambda: True)
    frame = user_frame()

    asyncio.run(processor.process_frame(frame, None))

    assert len(stub_llm.calls) == 1
    assert [f.message.role for f in captured] == ["user", "assistant"]


def test_turn_context_sits_before_the_final_user_message_and_stays_out_of_history(
    captured, stub_llm
):
    processor = an_llm_processor(turn_context=lambda: "[escalation: 7]")

    asyncio.run(processor.process_frame(user_frame("first"), None))
    messages = stub_llm.calls[0]["messages"]
    assert messages == [
        {"role": "system", "content": "[escalation: 7]"},
        {"role": "user", "content": "first"},
    ]

    # Second turn: the marker from turn one never persisted into history.
    asyncio.run(processor.process_frame(user_frame("second"), None))
    assert stub_llm.calls[1]["messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "I hear you."},
        {"role": "system", "content": "[escalation: 7]"},
        {"role": "user", "content": "second"},
    ]
    assert all(m.role in ("user", "assistant") for m in processor._history)


def test_turn_context_returning_none_injects_nothing(captured, stub_llm):
    processor = an_llm_processor(turn_context=lambda: None)

    asyncio.run(processor.process_frame(user_frame("hello"), None))

    assert stub_llm.calls[0]["messages"] == [{"role": "user", "content": "hello"}]


def test_event_sink_awaits_the_host_handler_for_both_roles(captured):
    handled = []

    async def handler(session_id, message):
        handled.append((session_id, message.role))

    sink = EventSinkProcessor(
        session_id="sess-1",
        emit=lambda payload: None,
        on_transcript_message=handler,
        transcript_event=lambda message: None,
    )

    asyncio.run(sink.process_frame(user_frame(), None))
    asyncio.run(
        sink.process_frame(
            TranscriptMessageFrame(
                TranscriptMessage(
                    role="assistant", content="ok", timestamp=datetime(2026, 1, 1)
                )
            ),
            None,
        )
    )

    assert handled == [("sess-1", "user"), ("sess-1", "assistant")]


def test_transcript_event_maps_what_goes_on_the_wire(captured):
    emitted = []
    sink = EventSinkProcessor(
        session_id="sess-1",
        emit=emitted.append,
        # Only assistant turns go over the data channel here.
        transcript_event=lambda m: (
            f'{{"said":"{m.content}"}}' if m.role == "assistant" else None
        ),
    )

    asyncio.run(sink.process_frame(user_frame("student speech"), None))
    assert emitted == []

    asyncio.run(
        sink.process_frame(
            TranscriptMessageFrame(
                TranscriptMessage(
                    role="assistant",
                    content="patient speech",
                    timestamp=datetime(2026, 1, 1),
                )
            ),
            None,
        )
    )
    assert emitted == ['{"said":"patient speech"}']


def test_event_sink_default_emits_raw_transcript_json(captured):
    emitted = []
    sink = EventSinkProcessor(session_id="sess-1", emit=emitted.append)
    frame = user_frame("hello")

    asyncio.run(sink.process_frame(frame, None))

    assert emitted == [frame.message.model_dump_json()]


def test_voice_speed_reaches_the_tts_factory(captured, monkeypatch):
    seen = {}

    class StubTTS:
        def stream_tts_sync(self, text):
            return iter(())

    def fake_get_tts_model(**kwargs):
        seen.update(kwargs)
        return StubTTS()

    monkeypatch.setattr(processors_module, "get_tts_model", fake_get_tts_model)
    processor = TTSProcessor(
        voice=VoiceConfig(
            provider="inworld", voice="Mark", model="inworld-tts-1.5-mini", speed=1.2
        )
    )

    asyncio.run(
        processor.process_frame(
            TranscriptMessageFrame(
                TranscriptMessage(
                    role="assistant", content="hello", timestamp=datetime(2026, 1, 1)
                )
            ),
            None,
        )
    )

    assert seen == {
        "provider": "inworld",
        "voice": "Mark",
        "model": "inworld-tts-1.5-mini",
        "speed": 1.2,
    }
