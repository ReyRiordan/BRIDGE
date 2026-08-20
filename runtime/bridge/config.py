"""
Game-engine configuration: plain environment reads plus the cached loaders for
the scenario and referee prompt.

Deliberately NOT a second pydantic-settings class. ``voice_kit.config`` owns the
kit's own settings (providers, keys, timeouts); `bridge` is container-only and
its handful of knobs are simple strings, so a plain ``os.environ`` read keeps
the two config surfaces from shadowing each other. ``amplify/constants.ts``
remains the single source of truth for the deployed values.

Every path falls back to a repo-relative default so tests and local runs work
with no environment at all; the deployed container sets the ``/app/resources``
paths explicitly.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

# runtime/bridge/config.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SCENARIO_PATH = os.environ.get("SCENARIO_PATH") or str(
    _REPO_ROOT / "resources" / "scenario_1.json"
)
REFEREE_PROMPT_PATH = os.environ.get("REFEREE_PROMPT_PATH") or str(
    _REPO_ROOT / "resources" / "referee.txt"
)
# The patient persona is assembled from two files: the prompt template (with a
# `{patient_name}` placeholder) and the case file it is filled from.
PATIENT_PROMPT_PATH = os.environ.get("PATIENT_PROMPT_PATH") or str(
    _REPO_ROOT / "resources" / "patient.txt"
)
PATIENT_CASE_PATH = os.environ.get("PATIENT_CASE_PATH") or str(
    _REPO_ROOT / "resources" / "patient.json"
)

# The referee is a separate LLM call from the patient agent (LLM_* in the kit's
# settings), so it carries its own provider/model/effort knobs.
REFEREE_PROVIDER = os.environ.get("REFEREE_PROVIDER", "openrouter")
REFEREE_MODEL = os.environ.get("REFEREE_MODEL", "anthropic/claude-haiku-4.5")
REFEREE_EFFORT = os.environ.get("REFEREE_EFFORT", "none")

# The referee sits on the serial critical path (STT -> referee -> patient -> TTS),
# so it fails open rather than making the student wait.
REFEREE_TIMEOUT_SECONDS = float(os.environ.get("REFEREE_TIMEOUT_SECONDS", "7.0"))

# How long a finished game keeps its container after `game_over` — enough for the
# client to render the debrief before the pipeline is torn down.
GAME_GRACE_SECONDS = float(os.environ.get("GAME_GRACE_SECONDS", "45.0"))

# Headroom on top of the game window when deriving the pipeline's idle timeout.
# The invariant the margin protects: idle timeout > time_limit + grace. pipecat
# CANCELS the pipeline on idle (closing the peer connection, with no end hook and
# no event to the browser), so an idle timeout landing inside a live game — or
# inside the post-`game_over` debrief window — reaches the student as a lost
# connection instead of the game's own ending. See `idle_timeout_for()`.
IDLE_TIMEOUT_MARGIN_SECONDS = float(
    os.environ.get("IDLE_TIMEOUT_MARGIN_SECONDS", "30.0")
)


def idle_timeout_for(scenario: dict) -> int:
    """The pipeline idle timeout that keeps this scenario's whole game alive."""
    return int(
        scenario["time_limit"] + GAME_GRACE_SECONDS + IDLE_TIMEOUT_MARGIN_SECONDS
    )


@lru_cache(maxsize=None)
def load_scenario(path: str = None) -> dict:
    """Load the scenario JSON (cached — it is immutable for the container's life)."""
    # utf-8 is explicit: the scenario's `desc`/`intro` strings carry curly quotes.
    with open(path or SCENARIO_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def load_referee_prompt(path: str = None) -> str:
    """Load the referee system prompt (cached)."""
    with open(path or REFEREE_PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


@lru_cache(maxsize=None)
def load_patient_prompt_template(path: str = None) -> str:
    """Load the patient prompt template, `{patient_name}` unsubstituted (cached)."""
    with open(path or PATIENT_PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


@lru_cache(maxsize=None)
def load_patient_case(path: str = None) -> dict:
    """Load the patient case file (cached)."""
    with open(path or PATIENT_CASE_PATH, encoding="utf-8") as f:
        return json.load(f)
