"""
GameSession point math, terminal logic, and the process-wide session registry.

Every bound comes from the real ``resources/scenario_1.json`` — the point values
under test are the ones the students actually play against, so a scenario edit
that breaks the game breaks these tests.
"""

import time

import pytest
from bridge import session as session_module
from bridge.config import load_scenario
from bridge.session import (
    GameSession,
    drop_session,
    get_or_create_session,
    get_session,
)

SCENARIO = load_scenario("scenario_1")
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


# --- conditional points, END SIMULATION, inactivity ---------------------------
# Against the real scenario_2 / scenario_3 configs, for the same reason as above.

CLINIC = load_scenario("scenario_2")
WARD = load_scenario("scenario_3")
WARD_KEY = "Acknowledge autonomy and outside pressures"
WARD_END = next(a for a in WARD["actions"] if a.get("ends_simulation"))


def a_ward(**kwargs) -> GameSession:
    return GameSession(session_id="w1", scenario=WARD, **kwargs)


def test_a_conditional_action_without_its_key_uses_point_change_without():
    session = a_ward()  # start 6
    action = session.apply_action("Explain risks in plain language")
    assert action["point_change"] == -1
    assert session.escalation == 5


def test_a_conditional_action_after_its_key_uses_point_change():
    session = a_ward()
    session.apply_action(WARD_KEY)  # -3 -> 3
    action = session.apply_action("Explain risks in plain language")
    assert action["point_change"] == -2
    assert session.escalation == 1


def test_the_key_counts_once_taken_even_after_it_is_cleared():
    session = GameSession(session_id="c1", scenario=CLINIC)  # start 5
    session.apply_action("Acknowledge frustration and apologize")  # -3 -> 2
    session.clear_transient_actions()  # the KEY is transient in scenario_2
    assert session.apply_action("Reset expectations")["point_change"] == -2
    assert session.escalation == 0


def test_apply_action_never_mutates_the_scenario():
    session = a_ward()
    session.apply_action("Explain risks in plain language")
    action = next(
        a for a in WARD["actions"] if a["type"] == "Explain risks in plain language"
    )
    assert action["point_change"] == -2


def test_an_end_simulation_action_fails_the_game_with_its_debrief():
    session = a_ward()
    action = session.apply_action(WARD_END["type"])
    assert action["point_change"] == 0
    assert session.escalation == 6
    assert session.status == "active"  # ends on check_terminal, like any other
    assert session.check_terminal() == ("fail", WARD_END["debrief"])
    assert session.status == "fail"
    assert session.check_terminal() is None


def test_end_simulation_wins_over_reaching_the_goal_in_the_same_turn():
    session = a_ward(escalation=1)
    session.apply_action(WARD_END["type"])
    session.apply_action("Offer control")  # -1 -> 0, the goal
    assert session.check_terminal() == ("fail", WARD_END["debrief"])


def test_inactivity_is_due_after_its_window():
    session = a_ward()
    assert session.apply_inactivity() is False
    session.last_helpful_at -= WARD["inactivity"]["seconds"]
    assert session.apply_inactivity() is True
    assert session.escalation == 7
    # The window restarts from the charge.
    assert session.apply_inactivity() is False


def test_a_helpful_action_restarts_the_inactivity_window():
    session = a_ward()
    session.last_helpful_at -= WARD["inactivity"]["seconds"] - 1
    session.apply_action("Offer control")  # helpful
    session.last_helpful_at -= 1
    assert session.apply_inactivity() is False


def test_a_neutral_or_harmful_action_does_not_restart_the_window():
    session = a_ward()
    session.last_helpful_at -= WARD["inactivity"]["seconds"]
    session.apply_action("Generic reassurance")  # 0
    session.apply_action("Fear-based coercion")  # +2 -> 8
    assert session.apply_inactivity() is True
    assert session.escalation == 9


def test_inactivity_is_a_no_op_without_the_block_or_after_the_end():
    plain = a_session()
    plain.last_helpful_at -= 10_000
    assert plain.apply_inactivity() is False

    ended = a_ward()
    ended.expire()
    ended.last_helpful_at -= 10_000
    assert ended.apply_inactivity() is False
