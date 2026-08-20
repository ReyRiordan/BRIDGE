"""
GET /scenario — the SPA's scenario config.

Loads `resources/scenario_1.json` once at module import — a broken or missing
bundle fails the Lambda cold start loudly instead of 500ing per request — and
serves the legacy 5-key whitelist the frontend derives everything from. No
Pydantic response model: the JSON is the source of truth, and the whitelist is
the contract.
"""

import json
import os
from pathlib import Path

from fastapi import APIRouter

# The image COPYs resources/ to /app/resources, next to /app/api, so the default
# resolves relative to this file — infra deliberately does NOT set SCENARIO_PATH
# on the Lambda (that var carries the *voice container's* /app/resources path,
# which does not exist here). The override exists for local runs.
DEFAULT_SCENARIO_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "scenario_1.json"
)

_SCENARIO_PATH = Path(os.getenv("SCENARIO_PATH") or DEFAULT_SCENARIO_PATH)
_scenario = json.loads(_SCENARIO_PATH.read_text())

router = APIRouter()


@router.get("/scenario")
def get_scenario() -> dict:
    """The whitelisted scenario config the SPA builds its whole UI from."""
    return {
        "intro": _scenario.get("intro", ""),
        "goal": _scenario.get("goal", ""),
        "actions": _scenario.get("actions", []),
        "point_bar": _scenario.get("point_bar", {}),
        "time_limit": _scenario.get("time_limit", 300),
    }
