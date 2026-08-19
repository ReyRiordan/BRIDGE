"""
The referee: detection, point application, event ordering, and fail-open.

No network and no pipeline — a ``StubLLM`` stands in for the provider and the
emitted events are captured through the emit callback, so what these tests
assert is exactly what the browser would receive on the data channel.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.config import load_scenario  # noqa: E402
from bridge.emitter import GameEvents  # noqa: E402
from bridge.referee import RefereeProcessor, build_response_format  # noqa: E402
from bridge.session import GameSession  # noqa: E402
from voice_kit.errors import UpstreamServiceError  # noqa: E402
from voice_kit.processors import TranscriptMessageFrame  # noqa: E402
from voice_kit.providers.llm import BaseLLM  # noqa: E402
from voice_kit.types import TranscriptMessage  # noqa: E402

SCENARIO = load_scenario()


class StubLLM(BaseLLM):
    """Captures every call and replies with a canned string (or raises/sleeps)."""

    def __init__(self, reply: str = '{"detected_actions": []}', error=None, delay=0.0):
        self.calls = []
        self._reply = reply
        self._error = error
        self._delay = delay

    async def chat(self, messages, system_prompt, response_format=None):
        self.calls.append(
            {
                "messages": messages,
                "system_prompt": system_prompt,
                "response_format": response_format,
            }
        )
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._reply


def reply_with(*types: str) -> str:
    return json.dumps({"detected_actions": [{"type": t} for t in types]})


class Harness:
    """A session + referee wired to a recording emit callback."""

    def __init__(self, llm, **kwargs):
        self.session = GameSession(session_id="s1", scenario=SCENARIO)
        self.sent = []
        self.events = GameEvents(
            "s1", lambda payload: self.sent.append(json.loads(payload))
        )
        self.game_over_calls = 0

        def on_game_over():
            self.game_over_calls += 1

        kwargs.setdefault("on_game_over", on_game_over)
        self.referee = RefereeProcessor(
            session=self.session,
            events=self.events,
            llm=llm,
            system_prompt="be a referee",
            **kwargs,
        )

    def score(self, utterance: str = "let me dim the lights"):
        asyncio.run(
            self.referee.score_turn(
                TranscriptMessage(
                    role="user", content=utterance, timestamp=datetime(2026, 1, 1)
                )
            )
        )
        return self.sent

    def types(self):
        return [e["type"] for e in self.sent]


def test_two_positive_actions_emit_two_detections_and_one_state_update():
    h = Harness(StubLLM(reply_with("Environmental", "Verbal Communication")))
    sent = h.score()

    assert h.types() == [
        "transcript_update",
        "action_detected",
        "action_detected",
        "state_update",
    ]
    assert sent[0]["role"] == "student"
    assert [e["action_type"] for e in sent[1:3]] == [
        "Environmental",
        "Verbal Communication",
    ]
    # -2 then -1 from a start of 5. The game continues: no game_over, so the
    # patient still replies this turn.
    assert sent[3]["escalation"] == 2
    assert set(sent[3]["active_actions"]) == {"Environmental", "Verbal Communication"}
    assert h.session.escalation == 2


def test_zero_detections_still_emit_state_and_clear_a_transient_action():
    """Regression: the legacy app emitted state only when something was detected,
    so a transient layer that just cleared never reached the client."""
    h = Harness(StubLLM(reply_with()))
    h.session.apply_action("Force IV")  # transient (persist: false)
    assert h.session.active_actions() == ["Force IV"]

    sent = h.score("how are you feeling?")
    assert h.types() == ["transcript_update", "state_update"]
    assert sent[-1]["active_actions"] == []
    assert h.session.escalation == 9  # unchanged by the no-detection turn


def test_a_redetected_transient_stays_lit():
    h = Harness(StubLLM(reply_with("Verbal Communication")))
    h.session.apply_action("Verbal Communication")
    sent = h.score()
    assert sent[-1]["active_actions"] == ["Verbal Communication"]


def test_fenced_json_still_parses():
    h = Harness(StubLLM("```json\n" + reply_with("Environmental") + "\n```"))
    h.score()
    assert h.session.escalation == 3


def test_duplicate_types_are_deduped():
    h = Harness(StubLLM(reply_with("Environmental", "Environmental")))
    h.score()
    assert h.types().count("action_detected") == 1
    assert h.session.escalation == 3


def test_unknown_types_are_ignored():
    h = Harness(StubLLM(reply_with("Telepathy", "Environmental")))
    h.score()
    assert h.types().count("action_detected") == 1
    assert h.session.escalation == 3


def test_model_supplied_point_change_is_ignored():
    """Points come from the scenario; a model-supplied number is never trusted."""
    h = Harness(
        StubLLM(
            json.dumps(
                {
                    "detected_actions": [
                        {"type": "Environmental", "point_change": -99},
                    ]
                }
            )
        )
    )
    sent = h.score()
    assert sent[1]["point_change"] == -2
    assert h.session.escalation == 3


@pytest.mark.parametrize(
    "llm",
    [
        StubLLM("not json at all"),
        StubLLM('{"detected_actions": [{"nope": 1}]}'),
        StubLLM(error=UpstreamServiceError("provider is down", upstream_status=503)),
        StubLLM(error=RuntimeError("something unforeseen")),
    ],
    ids=["malformed", "schema-violation", "upstream-error", "unexpected-error"],
)
def test_failures_fail_open(llm):
    h = Harness(llm)
    sent = h.score()
    assert h.types() == ["transcript_update", "state_update"]
    assert h.session.escalation == 5
    assert h.session.status == "active"
    assert sent[-1]["escalation"] == 5


def test_timeout_fails_open():
    h = Harness(StubLLM(reply_with("Environmental"), delay=0.2), timeout_seconds=-0.9)
    sent = h.score()
    assert h.types() == ["transcript_update", "state_update"]
    assert h.session.escalation == 5
    assert sent[-1]["escalation"] == 5


def test_terminal_turn_emits_game_over_once():
    h = Harness(StubLLM(reply_with("Restraint")))  # +10 -> clamps to max
    sent = h.score()
    assert h.types() == [
        "transcript_update",
        "action_detected",
        "state_update",
        "game_over",
    ]
    assert sent[-1] == {
        "v": 1,
        "type": "game_over",
        "status": "fail",
        "reason": "Escalation reached maximum",
    }
    assert h.game_over_calls == 1
    assert h.session.status == "fail"


def test_success_terminal():
    h = Harness(StubLLM(reply_with("Caregiver involvement", "Environmental")))
    sent = h.score()
    assert sent[-1] == {
        "v": 1,
        "type": "game_over",
        "status": "success",
        "reason": "Escalation reduced to goal",
    }


def test_a_finished_session_only_emits_the_student_transcript():
    h = Harness(StubLLM(reply_with("Environmental")))
    h.session.escalation = 0
    h.session.check_terminal()
    h.score()
    assert h.types() == ["transcript_update"]
    assert h.game_over_calls == 0


def test_the_prompt_withholds_point_values_and_pins_the_schema():
    llm = StubLLM(reply_with())
    h = Harness(llm)
    h.score("let me dim the lights")

    call = llm.calls[0]
    assert call["system_prompt"] == "be a referee"
    payload = json.loads(call["messages"][0]["content"])
    assert payload["utterance"] == "let me dim the lights"
    assert payload["escalation"] == 5
    assert all(set(a) == {"type", "desc"} for a in payload["actions"])

    schema = call["response_format"]["json_schema"]["schema"]
    enum = schema["properties"]["detected_actions"]["items"]["properties"]["type"][
        "enum"
    ]
    assert enum == [a["type"] for a in SCENARIO["actions"]]
    assert len(enum) == 9
    assert schema["additionalProperties"] is False


def test_response_format_is_built_from_the_scenario():
    schema = build_response_format({"actions": [{"type": "Only one"}]})
    items = schema["json_schema"]["schema"]["properties"]["detected_actions"]["items"]
    assert items["properties"]["type"]["enum"] == ["Only one"]
    assert items["additionalProperties"] is False


def test_process_frame_pushes_the_user_frame_after_scoring(monkeypatch):
    """The patient LLM and the transcript sink must still see the turn."""
    h = Harness(StubLLM(reply_with("Environmental")))
    order = []

    async def fake_push(self, frame, direction=None):
        order.append(("push", frame))

    monkeypatch.setattr(RefereeProcessor, "push_frame", fake_push, raising=False)

    message = TranscriptMessage(
        role="user", content="dim the lights", timestamp=datetime(2026, 1, 1)
    )
    frame = TranscriptMessageFrame(message)

    async def run():
        await h.referee.process_frame(frame, None)

    asyncio.run(run())

    assert h.types() == ["transcript_update", "action_detected", "state_update"]
    assert order == [("push", frame)]
