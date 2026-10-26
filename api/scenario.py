"""
GET /scenarios + GET /scenario/{scenario_id} — the SPA's scenario configs.

Loads every `resources/scenarios/<id>/scenario.json` once at module import — a
broken or missing bundle fails the Lambda cold start loudly instead of 500ing
per request. `/scenarios` is the Start screen's picker list; `/scenario/{id}`
serves the whitelist the frontend derives everything from. No Pydantic response
model: the JSON is the source of truth, and the whitelist is the contract.
"""

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

# The image COPYs resources/ to /app/resources, next to /app/api, so the default
# resolves relative to this file — infra deliberately does NOT set SCENARIOS_DIR
# on the Lambda (that var carries the *voice container's* /app/resources path,
# which does not exist here). The override exists for local runs.
DEFAULT_SCENARIOS_DIR = (
    Path(__file__).resolve().parent.parent / "resources" / "scenarios"
)

_SCENARIOS_DIR = Path(os.getenv("SCENARIOS_DIR") or DEFAULT_SCENARIOS_DIR)


def _load_all(root: Path) -> dict[str, dict]:
    """Every scenario folder, keyed by folder name (which must match its `id`)."""
    scenarios = {}
    for path in sorted(root.glob("*/scenario.json")):
        # utf-8 is explicit: the scenario's `desc`/`intro` strings carry curly quotes.
        body = json.loads(path.read_text(encoding="utf-8"))
        folder = path.parent.name
        if body.get("id") != folder:
            raise ValueError(f"{path}: id {body.get('id')!r} != folder {folder!r}")
        scenarios[folder] = body
    if not scenarios:
        raise FileNotFoundError(f"no scenarios under {root}")
    return scenarios


_scenarios = _load_all(_SCENARIOS_DIR)

router = APIRouter()


@router.get("/scenarios")
def list_scenarios() -> list[dict]:
    """The picker: one card per scenario, in id order."""
    return [
        {"id": s["id"], "title": s["title"], "intro_image": s["intro_image"]}
        for s in _scenarios.values()
    ]


@router.get("/scenario/{scenario_id}")
def get_scenario(scenario_id: str) -> dict:
    """The whitelisted scenario config the SPA builds its whole UI from."""
    s = _scenarios.get(scenario_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario {scenario_id!r}")
    return {
        "id": s["id"],
        "title": s["title"],
        "intro": s.get("intro", ""),
        "goal": s.get("goal", ""),
        "intro_image": s["intro_image"],
        "environment": s.get("environment"),
        "patient_frame_ext": s.get("patient_frame_ext", "png"),
        "inactivity": s.get("inactivity"),
        "actions": s.get("actions", []),
        "point_bar": s.get("point_bar", {}),
        "time_limit": s.get("time_limit", 300),
    }
