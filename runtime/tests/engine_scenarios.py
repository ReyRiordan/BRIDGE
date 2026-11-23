"""Shipped scenarios with the bar's start pinned, for engine-arithmetic tests.

The engine tests assert exact escalation values after actions, so they must not
move when the scenario content is retuned. `engine_scenario` returns a copy of
the real scenario (real actions, real point values) with `point_bar.start`
fixed at ENGINE_START. The shipped start value itself is pinned once, in
`test_config.py`.
"""

import copy

from bridge.config import load_scenario

ENGINE_START = 5


def engine_scenario(scenario_id: str) -> dict:
    scenario = copy.deepcopy(load_scenario(scenario_id))
    scenario["point_bar"]["start"] = ENGINE_START
    return scenario
