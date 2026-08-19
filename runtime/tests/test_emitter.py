"""
The emitter: v1 envelope per helper, failure isolation, and the sink mapper.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.config import load_scenario  # noqa: E402
from bridge.emitter import GameEvents, transcript_event  # noqa: E402
from bridge.session import GameSession  # noqa: E402
from voice_kit.types import TranscriptMessage  # noqa: E402

SCENARIO = load_scenario()


class Recorder:
    def __init__(self):
        self.sent = []

    def __call__(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def test_transcript_event_envelope():
    sent = Recorder()
    GameEvents("s1", sent).transcript(
        "student", "I can dim the lights", datetime(2026, 1, 1, 12, 30)
    )
    assert sent.sent == [
        {
            "v": 1,
            "type": "transcript_update",
            "role": "student",
            "content": "I can dim the lights",
            "timestamp": "2026-01-01T12:30:00",
        }
    ]


def test_action_event_carries_the_scenario_desc_and_points():
    sent = Recorder()
    action = next(a for a in SCENARIO["actions"] if a["type"] == "Acknowledge distress")
    GameEvents("s1", sent).action(action)
    assert sent.sent == [
        {
            "v": 1,
            "type": "action_detected",
            "action_type": "Acknowledge distress",
            # Curly quotes, straight from the utf-8 scenario file.
            "desc": "E.g. “I see this is overwhelming”",
            "point_change": -1,
        }
    ]


def test_state_event_projects_the_session():
    sent = Recorder()
    session = GameSession(session_id="s1", scenario=SCENARIO)
    session.apply_action("Environmental")
    GameEvents("s1", sent).state(session)
    assert sent.sent == [
        {
            "v": 1,
            "type": "state_update",
            "escalation": 3,
            "max": 10,
            "active_actions": ["Environmental"],
            "status": "active",
        }
    ]


def test_timer_and_game_over_envelopes():
    sent = Recorder()
    events = GameEvents("s1", sent)
    events.timer(12, 300)
    events.game_over("fail", "Time limit reached")
    assert sent.sent == [
        {"v": 1, "type": "timer", "elapsed": 12, "limit": 300},
        {"v": 1, "type": "game_over", "status": "fail", "reason": "Time limit reached"},
    ]


def test_emit_none_is_a_no_op():
    GameEvents("s1", None).timer(1, 300)  # must not raise


def test_a_raising_emit_is_swallowed(caplog):
    def boom(_payload):
        raise RuntimeError("data channel is closed")

    events = GameEvents("s1", boom)
    events.timer(1, 300)  # first failure: warning
    events.timer(2, 300)  # subsequent: debug, so a dead channel stays quiet
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1


def test_transcript_event_maps_assistant_to_patient():
    payload = transcript_event(
        TranscriptMessage(
            role="assistant", content="Leave me alone", timestamp=datetime(2026, 1, 1)
        )
    )
    assert json.loads(payload) == {
        "v": 1,
        "type": "transcript_update",
        "role": "patient",
        "content": "Leave me alone",
        "timestamp": "2026-01-01T00:00:00",
    }


def test_transcript_event_drops_user_turns():
    """The referee already emitted the student's utterance, before scoring."""
    assert (
        transcript_event(
            TranscriptMessage(
                role="user",
                content="Can I dim the lights?",
                timestamp=datetime(2026, 1, 1),
            )
        )
        is None
    )
