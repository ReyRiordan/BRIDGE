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

Each scenario is one folder under ``SCENARIOS_DIR`` —
``<id>/{scenario.json, patient.txt, patient.json}`` — and the set of folder
names is the scenario allow-list. The SPA picks a scenario and the runtime only
ever receives the session id, so the id carries the scenario as a prefix
(``<scenario_id>--<uuid>``, see ``parse_session_id``). That is a prototype
shortcut: the vendored kit has no per-session metadata channel to carry it.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

# runtime/bridge/config.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SCENARIOS_DIR = os.environ.get("SCENARIOS_DIR") or str(
    _REPO_ROOT / "resources" / "scenarios"
)
REFEREE_PROMPT_PATH = os.environ.get("REFEREE_PROMPT_PATH") or str(
    _REPO_ROOT / "resources" / "referee.txt"
)

# `<scenario_id>--<uuid>`: split on the FIRST separator only.
SESSION_ID_SEPARATOR = "--"

# The referee is a separate LLM call from the patient agent (LLM_* in the kit's
# settings), so it carries its own provider/model/reasoning trio — same three
# concepts, same suffixes, REFEREE_ prefix instead of LLM_.
REFEREE_PROVIDER = os.environ.get("REFEREE_PROVIDER", "openrouter")
REFEREE_MODEL = os.environ.get("REFEREE_MODEL", "anthropic/claude-haiku-4.5")
REFEREE_REASONING = os.environ.get("REFEREE_REASONING", "none")

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
def _discover_scenarios() -> tuple:
    root = Path(SCENARIOS_DIR)
    return tuple(
        sorted(p.name for p in root.iterdir() if (p / "scenario.json").is_file())
    )


def scenario_ids() -> list:
    """The scenario allow-list: every folder under ``SCENARIOS_DIR``, sorted."""
    return list(_discover_scenarios())


def parse_session_id(session_id: str) -> str:
    """The scenario id a session id carries as its ``<id>--`` prefix.

    Raises ``ValueError`` for a missing or unknown prefix — there is no fallback
    scenario, so a client that forgot the prefix fails loudly at connect.
    """
    prefix, sep, _ = session_id.partition(SESSION_ID_SEPARATOR)
    if not sep or prefix not in scenario_ids():
        raise ValueError(
            f"session id {session_id!r} does not start with a known scenario id "
            f"followed by {SESSION_ID_SEPARATOR!r} (known: {scenario_ids()})"
        )
    return prefix


def _scenario_file(scenario_id: str, name: str) -> Path:
    if scenario_id not in scenario_ids():
        raise ValueError(f"unknown scenario id {scenario_id!r}")
    return Path(SCENARIOS_DIR) / scenario_id / name


@lru_cache(maxsize=None)
def load_scenario(scenario_id: str) -> dict:
    """Load one scenario's JSON (cached — it is immutable for the container's life)."""
    # utf-8 is explicit: the scenario's `desc`/`intro` strings carry curly quotes.
    with open(_scenario_file(scenario_id, "scenario.json"), encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def load_referee_prompt(path: str = None) -> str:
    """Load the referee system prompt (cached). Shared by every scenario."""
    with open(path or REFEREE_PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


@lru_cache(maxsize=None)
def load_patient_prompt_template(scenario_id: str) -> str:
    """Load a scenario's patient template, `{patient_name}` unsubstituted (cached)."""
    with open(_scenario_file(scenario_id, "patient.txt"), encoding="utf-8") as f:
        return f.read()


@lru_cache(maxsize=None)
def load_patient_case(scenario_id: str) -> dict:
    """Load a scenario's patient case file (cached)."""
    with open(_scenario_file(scenario_id, "patient.json"), encoding="utf-8") as f:
        return json.load(f)
