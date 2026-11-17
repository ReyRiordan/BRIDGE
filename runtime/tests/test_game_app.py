"""
The runtime wiring: context resume, the processor chain, and the session hooks.

``bridge.app`` is the uvicorn/Docker target; importing it registers the game
engine on the kit's extension points.
"""

import asyncio
import json
from datetime import datetime

import pytest
from bridge import session as session_module
from bridge import timer as timer_module
from bridge.app import (
    build_game_processors,
    on_session_end,
    on_session_start,
    provide_context,
    store_turn,
)
from bridge.config import GAME_GRACE_SECONDS, load_scenario
from bridge.referee import RefereeProcessor
from voice_kit import ProcessorFactoryArgs, TranscriptMessage
from voice_kit.processors import (
    EventSinkProcessor,
    LLMProcessor,
    STTProcessor,
    TTSProcessor,
)

# The SPA prefixes every session id with the scenario it picked.
SID = "scenario_1--s1"


@pytest.fixture(autouse=True)
def clean_state():
    session_module._sessions.clear()
    timer_module._timers.clear()
    timer_module._reapers.clear()
    yield
    session_module._sessions.clear()
    timer_module._timers.clear()
    timer_module._reapers.clear()


def test_context_carries_the_scenario_voice_time_limit_and_session():
    context = asyncio.run(provide_context(SID))
    assert context.voice.provider == "inworld"
    assert context.voice.speed == 1.2
    assert context.time_limit_seconds == 300
    assert context.system_prompt.strip()
    assert context.metadata["game"] is session_module.get_session(SID)


@pytest.mark.parametrize(
    "scenario_id, voice",
    [("scenario_2", "Brandon"), ("scenario_3", "community-wcnfpq1cmfky")],
)
def test_the_session_id_prefix_selects_the_scenario(scenario_id, voice):
    context = asyncio.run(provide_context(f"{scenario_id}--abc"))
    session = context.metadata["game"]
    assert session.scenario["id"] == scenario_id
    assert session.escalation == load_scenario(scenario_id)["point_bar"]["start"]
    assert context.voice.voice == voice
    assert context.system_prompt != asyncio.run(provide_context(SID)).system_prompt


@pytest.mark.parametrize("session_id", ["s1", "nope--s1", "--s1", "scenario_9--s1"])
def test_an_unprefixed_or_unknown_session_id_fails_loudly(session_id):
    with pytest.raises(ValueError):
        asyncio.run(provide_context(session_id))
    assert session_module._sessions == {}


def test_the_idle_backstop_outlives_the_whole_game_window():
    """The regression guard for "Connection Lost" at the time limit.

    pipecat cancels the pipeline on idle, closing the peer connection with no
    end hook and no event to the browser. A run to the time limit is silent by
    definition, so an idle timeout inside `time_limit + grace` kills the data
    channel before `game_over` is emitted and the student sees a dropped
    connection instead of the timeout debrief.
    """
    scenario = load_scenario("scenario_1")
    context = asyncio.run(provide_context(SID))
    assert context.idle_timeout_seconds > scenario["time_limit"] + GAME_GRACE_SECONDS


def test_a_rebuild_on_a_warm_container_resumes_the_same_session():
    first = asyncio.run(provide_context(SID))
    session = first.metadata["game"]
    session.apply_action("Environmental")
    asyncio.run(
        store_turn(
            SID,
            TranscriptMessage(
                role="user", content="dim the lights", timestamp=datetime(2026, 1, 1)
            ),
        )
    )

    second = asyncio.run(provide_context(SID))
    assert second.metadata["game"] is session
    assert second.metadata["game"].escalation == 3
    # The prior transcript seeds the patient LLM so it resumes mid-conversation.
    assert [m.content for m in second.initial_history] == ["dim the lights"]


def test_store_turn_ignores_an_unknown_session():
    asyncio.run(
        store_turn(
            "gone",
            TranscriptMessage(
                role="user", content="hello", timestamp=datetime(2026, 1, 1)
            ),
        )
    )


def a_factory_args(emit=None) -> ProcessorFactoryArgs:
    context = asyncio.run(provide_context(SID))
    return ProcessorFactoryArgs(
        session_id=SID,
        session_context=context,
        system_prompt=context.system_prompt,
        voice=context.voice,
        initial_history=context.initial_history,
        emit=emit,
    )


def test_the_chain_splices_the_referee_between_stt_and_the_patient_llm():
    processors = build_game_processors(a_factory_args())
    assert [type(p) for p in processors] == [
        STTProcessor,
        RefereeProcessor,
        LLMProcessor,
        TTSProcessor,
        EventSinkProcessor,
    ]


def test_the_patient_turn_is_gated_and_carries_the_escalation_marker():
    args = a_factory_args()
    session = args.session_context.metadata["game"]
    llm = build_game_processors(args)[2]

    assert llm._turn_gate() is True
    assert llm._turn_context() == "[CURRENT ESCALATION: 5/10]"

    session.escalation = 0
    session.check_terminal()
    # A finished game still transcribes the student; the patient says nothing.
    assert llm._turn_gate() is False


def test_the_sink_emits_patient_turns_only():
    sink = build_game_processors(a_factory_args())[4]
    assert (
        sink._transcript_event(
            TranscriptMessage(role="user", content="hi", timestamp=datetime(2026, 1, 1))
        )
        is None
    )
    assert sink._transcript_event(
        TranscriptMessage(
            role="assistant", content="go away", timestamp=datetime(2026, 1, 1)
        )
    )


class FakeTransport:
    """Captures the handler registered for the transport's connect event."""

    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def register(fn):
            self.handlers[name] = fn
            return fn

        return register


class FakeTask:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


class FakePipelineContext:
    def __init__(self, game):
        self.game = game
        self.task = FakeTask()


def test_session_start_arms_the_clock_and_emits_state_on_connect():
    context = asyncio.run(provide_context(SID))
    session = context.metadata["game"]
    session.apply_action("Environmental")
    sent = []
    transport = FakeTransport()

    async def run():
        await on_session_start(
            SID,
            FakePipelineContext(session),
            transport,
            lambda payload: sent.append(json.loads(payload)),
        )
        # The data channel is not open yet: nothing is emitted inline.
        assert sent == []
        await transport.handlers["on_client_connected"](transport, None)
        timer_module.cancel_timer(SID)

    asyncio.run(run())

    assert timer_module._timers == {}
    assert sent == [
        {
            "v": 1,
            "type": "state_update",
            "escalation": 3,
            "max": 10,
            "active_actions": ["Environmental"],
            "status": "active",
        }
    ]


def test_session_start_routes_typed_text_into_the_pipeline():
    session = asyncio.run(provide_context(SID)).metadata["game"]
    context = FakePipelineContext(session)
    transport = FakeTransport()

    async def run():
        await on_session_start(SID, context, transport, lambda _: None)
        handler = transport.handlers["on_app_message"]
        await handler(
            transport, {"v": 1, "type": "student_text", "content": " hi "}, "pc"
        )
        # Malformed or foreign messages are dropped, never raised.
        await handler(transport, {"v": 2, "type": "student_text", "content": "x"}, "pc")
        await handler(transport, "ping", "pc")
        timer_module.cancel_timer(SID)

    asyncio.run(run())

    (frame,) = context.task.frames
    assert frame.message.role == "user"
    assert frame.message.content == "hi"
    assert frame.message.source == "text"


# --- session record wiring --------------------------------------------------


class RecordingTable:
    def __init__(self):
        self.updates = []

    def update(self, session_id, update):
        self.updates.append(update)


@pytest.fixture
def table(monkeypatch):
    t = RecordingTable()
    monkeypatch.setattr("bridge.app.get_session_table", lambda: t)
    return t


def start(session):
    async def run():
        await on_session_start(
            SID, FakePipelineContext(session), FakeTransport(), lambda _: None
        )
        timer_module.cancel_timer(SID)

    return run()


def test_a_reconnect_reuses_the_recorder_and_records_one_start(table):
    async def run():
        first = (await provide_context(SID)).metadata["game"]
        recorder = first.recorder
        await start(first)
        second = (await provide_context(SID)).metadata["game"]
        await start(second)
        assert second.recorder is recorder
        await recorder.close()

    asyncio.run(run())
    starts = [u for u in table.updates if u.get("set", {}).get("status") == "active"]
    assert len(starts) == 1


def test_store_turn_records_patient_turns_only(table):
    async def run():
        session = (await provide_context(SID)).metadata["game"]
        for role in ("user", "assistant"):
            await store_turn(
                SID,
                TranscriptMessage(
                    role=role, content=role, timestamp=datetime(2026, 1, 1)
                ),
            )
        await session.recorder.close()

    asyncio.run(run())
    turns = [t for u in table.updates for t in u.get("append", {}).get("turns", [])]
    assert [(t["role"], t["content"]) for t in turns] == [("patient", "assistant")]


def test_session_end_marks_an_active_session_abandoned(table):
    async def run():
        session = (await provide_context(SID)).metadata["game"]
        await start(session)
        await on_session_end(SID)

    asyncio.run(run())
    assert table.updates[-1]["set"]["status"] == "abandoned"
    assert table.updates[-1]["set"]["end_reason"] == "abandoned"
    assert session_module.get_session(SID) is None


def test_session_end_after_game_over_is_not_abandoned(table):
    async def run():
        session = (await provide_context(SID)).metadata["game"]
        await start(session)
        session.escalation = 0
        session.check_terminal()
        session.recorder.ended(session)
        await on_session_end(SID)

    asyncio.run(run())
    statuses = [u["set"].get("status") for u in table.updates if "set" in u]
    assert statuses == ["active", "success"]
