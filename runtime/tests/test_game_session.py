"""
GameSession point math, terminal logic, and the process-wide session registry.

Every bound comes from the real ``resources/scenario_1.json`` — the point values
under test are the ones the students actually play against, so a scenario edit
that breaks the game breaks these tests.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import session as session_module  # noqa: E402
from bridge.config import load_scenario  # noqa: E402
from bridge.session import (  # noqa: E402
    GameSession,
    drop_session,
    get_or_create_session,
    get_session,
)

SCENARIO = load_scenario()
ACTIONS = {a["type"]: a for a in SCENARIO["actions"]}


@pytest.fixture(autouse=True)
def clean_registry():
    """The registry is module-global — never leak a session between tests."""
    session_module._sessions.clear()
    yield
    session_module._sessions.clear()


def a_session(session_id: str = "s1") -> GameSession:
    return GameSession(session_id=session_id, scenario=SCENARIO)


def test_seeds_bounds_from_the_scenario():
    session = a_session()
    assert session.escalation == SCENARIO["point_bar"]["start"] == 5
    assert session.max_escalation == 10
    assert session.goal == 0
    assert session.time_limit == SCENARIO["time_limit"] == 300
    assert session.status == "active"


def test_apply_action_uses_the_scenario_point_change():
    session = a_session()
    action = session.apply_action("Caregiver involvement")
    assert action["point_change"] == -3
    assert session.escalation == 2
    assert session.active_actions() == ["Caregiver involvement"]
    assert session.actions_ever_taken == ["Caregiver involvement"]


def test_apply_action_clamps_at_zero_and_max():
    session = a_session()
    session.apply_action("Caregiver involvement")  # -3 -> 2
    session.apply_action("Environmental")  # -2 -> 0 (clamped)
    session.apply_action("Verbal Communication")  # -1 -> stays 0
    assert session.escalation == 0

    session = a_session()
    # Restraint is +10 from a start of 5: clamping to max means an instant fail.
    # That is the intended design, not an off-by-one.
    assert ACTIONS["Restraint"]["point_change"] == 10
    session.apply_action("Restraint")
    assert session.escalation == 10


def test_unknown_action_is_a_no_op():
    session = a_session()
    assert session.apply_action("Telepathy") is None
    assert session.escalation == 5
    assert session.active_actions() == []


def test_clear_transient_actions_keeps_persisting_ones():
    session = a_session()
    session.apply_action("Environmental")  # persist: true
    session.apply_action("Force IV")  # persist: false
    assert set(session.active_actions()) == {"Environmental", "Force IV"}

    session.clear_transient_actions()
    assert session.active_actions() == ["Environmental"]
    # Cleared, but still remembered as having happened.
    assert session.actions_ever_taken == ["Environmental", "Force IV"]


def test_terminal_success_at_goal():
    session = a_session()
    session.escalation = 0
    assert session.check_terminal() == ("success", "Escalation reduced to goal")
    assert session.status == "success"
    assert session.ended_at is not None
    # Already terminal: no second transition.
    assert session.check_terminal() is None


def test_terminal_fail_at_max():
    session = a_session()
    session.escalation = 10
    assert session.check_terminal() == ("fail", "Escalation reached maximum")
    assert session.status == "fail"


def test_no_terminal_mid_bar():
    session = a_session()
    session.apply_action("Verbal Communication")
    assert session.check_terminal() is None
    assert session.status == "active"


def test_expire_fails_the_session():
    session = a_session()
    assert session.expire() == ("fail", "Time limit reached")
    assert session.status == "fail"
    # Idempotent for an already-ended session.
    assert session.expire() == ("fail", "Time limit reached")


def test_elapsed_seconds_runs_from_started_at():
    session = a_session()
    session.started_at = time.monotonic() - 42
    assert session.elapsed_seconds() == 42


def test_to_state_update_projects_the_v1_envelope():
    session = a_session()
    session.apply_action("Environmental")
    event = session.to_state_update()
    assert event.v == 1
    assert event.type == "state_update"
    assert (event.escalation, event.max) == (3, 10)
    assert event.active_actions == ["Environmental"]
    assert event.status == "active"


def test_registry_reuses_an_existing_session():
    first = get_or_create_session("s1", SCENARIO)
    first.apply_action("Environmental")
    second = get_or_create_session("s1", SCENARIO)
    assert second is first
    assert second.escalation == 3


def test_registry_reuses_a_terminal_session():
    """A reconnect after game over must show the ending, not restart the game."""
    session = get_or_create_session("s1", SCENARIO)
    session.escalation = 0
    session.check_terminal()
    resumed = get_or_create_session("s1", SCENARIO)
    assert resumed is session
    assert resumed.status == "success"


def test_drop_session():
    get_or_create_session("s1", SCENARIO)
    assert drop_session("s1") is not None
    assert get_session("s1") is None
    assert drop_session("s1") is None


def test_sweep_drops_finished_and_aged_out_sessions():
    from bridge.config import GAME_GRACE_SECONDS

    finished = get_or_create_session("finished", SCENARIO)
    finished.check_terminal()  # not terminal yet at 5...
    finished.escalation = 0
    finished.check_terminal()
    finished.ended_at = time.monotonic() - (GAME_GRACE_SECONDS + 1)

    aged = get_or_create_session("aged", SCENARIO)
    aged.started_at = time.monotonic() - (2 * SCENARIO["time_limit"] + 1)

    live = get_or_create_session("live", SCENARIO)

    # The sweep runs on every create.
    get_or_create_session("new", SCENARIO)
    assert get_session("finished") is None
    assert get_session("aged") is None
    assert get_session("live") is live


def test_sweep_keeps_a_session_inside_its_grace_window():
    session = get_or_create_session("s1", SCENARIO)
    session.escalation = 0
    session.check_terminal()
    get_or_create_session("other", SCENARIO)
    assert get_session("s1") is session
