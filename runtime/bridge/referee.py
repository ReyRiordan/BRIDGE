"""
The referee: scores each student utterance against the scenario's actions.

Sits between STT and the patient LLM, so it runs on the serial critical path of
every turn (STT -> referee -> patient -> TTS). Two consequences shape the whole
module:

- **It fails open.** A timeout, a provider error, malformed JSON — anything —
  scores the turn as "no actions detected" and lets the conversation continue.
  A referee bug must never stop the patient from replying.
- **It emits directly**, through the data-channel callback, rather than pushing
  event frames downstream. Scoring completes before the frame is handed to the
  patient LLM and ``send_app_message`` is an ordered send, so the required event
  ordering holds by construction — while frame-path emission would queue every
  referee event behind the fully-blocking TTS stage.

The model is asked only for action *types*, constrained by a strict json_schema
whose enum is generated from the scenario. Point values are never accepted from
the model: the server looks them up in the scenario itself.
"""

import asyncio
import inspect
import json
import logging
import re
import time
from typing import Callable, List, Optional

from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pydantic import BaseModel, ConfigDict
from voice_kit.config import settings
from voice_kit.processors import TranscriptMessageFrame
from voice_kit.providers.llm import OpenRouterChat, get_llm_model
from voice_kit.types import TranscriptMessage

from . import config

logger = logging.getLogger(__name__)

# Legacy defensive strip: even with structured output a model can wrap its JSON
# in a markdown fence, and one stray fence must not cost the student a turn.
_FENCE_RE = re.compile(r"```(?:json)?\s*")


class DetectedAction(BaseModel):
    """One scored action. ``extra="ignore"`` so a model that also echoes the
    legacy point/visual fields validates instead of failing the turn."""

    model_config = ConfigDict(extra="ignore")

    type: str


class RefereeVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    detected_actions: List[DetectedAction] = []


def build_referee_payload(scenario: dict, utterance: str, escalation: int) -> str:
    """The referee's user message: the utterance, the escalation, the action list.

    The raw ``point_change`` is deliberately withheld — a model that cannot see
    the numbers cannot tempt us into trusting the ones it echoes. Only its SIGN
    is exposed, as ``escalates``, which is what lets the prompt say "be strict
    with escalating actions" without naming any of them: adding an action to the
    scenario never requires a prompt edit.
    """
    return json.dumps(
        {
            "utterance": utterance,
            "escalation": escalation,
            "actions": [
                {
                    "type": a["type"],
                    "desc": a["desc"],
                    "escalates": a["point_change"] > 0,
                }
                for a in scenario["actions"]
            ],
        }
    )


def build_response_format(scenario: dict) -> dict:
    """Strict json_schema for the verdict, with the enum taken from the scenario."""
    action_types = [a["type"] for a in scenario["actions"]]
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "referee_verdict",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "detected_actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": action_types}
                            },
                            "required": ["type"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["detected_actions"],
                "additionalProperties": False,
            },
        },
    }


def build_referee_llm(timeout_seconds: float):
    """The referee's own LLM client (separate model/effort from the patient agent).

    OpenRouter routes a model to whichever backend serves it, and one that does
    not support ``response_format`` would reject the request — so the referee
    always asks for ``require_parameters``, restricting routing to backends that
    honour every parameter we send.
    """
    if config.REFEREE_PROVIDER == "openrouter":
        return OpenRouterChat(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            model=config.REFEREE_MODEL,
            reasoning_effort=config.REFEREE_REASONING,
            timeout_seconds=timeout_seconds,
            require_parameters=True,
        )
    return get_llm_model(
        provider=config.REFEREE_PROVIDER,
        model=config.REFEREE_MODEL,
        reasoning_effort=config.REFEREE_REASONING,
        timeout_seconds=timeout_seconds,
    )


class RefereeProcessor(FrameProcessor):
    """Score every student turn, mutate the session, emit the resulting events."""

    def __init__(
        self,
        session,
        events,
        llm=None,
        system_prompt: Optional[str] = None,
        timeout_seconds: float = config.REFEREE_TIMEOUT_SECONDS,
        on_game_over: Optional[Callable[[], object]] = None,
    ):
        super().__init__()
        self._session = session
        self._events = events
        self._timeout_seconds = timeout_seconds
        self._on_game_over = on_game_over
        self._system_prompt = (
            system_prompt if system_prompt is not None else config.load_referee_prompt()
        )
        # Client and schema are built once: neither depends on the turn.
        self._llm = llm if llm is not None else build_referee_llm(timeout_seconds)
        self._response_format = build_response_format(session.scenario)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptMessageFrame) and frame.message.role == "user":
            await self.score_turn(frame.message)

        # Always last, always unconditional: the patient LLM (and the transcript
        # sink) must see the turn even when the game just ended — the LLM stage's
        # turn gate is what suppresses the reply.
        await self.push_frame(frame, direction)

    async def score_turn(self, message: TranscriptMessage) -> None:
        """Referee one utterance: emit, detect, apply, publish state, check terminal."""
        session = self._session
        started = time.perf_counter()

        # Before any scoring — the transcript must never wait on the LLM.
        self._events.transcript("student", message.content, message.timestamp)

        if session.status != "active":
            # Transcript preserved, nothing scored, no duplicate state_update.
            return

        # Before applying, so an action re-earned this turn stays lit.
        session.clear_transient_actions()

        detected = await self._detect(message.content)

        for action_type in detected:
            action = session.apply_action(action_type)
            if action is None:
                continue
            self._events.action(action)

        # UNCONDITIONAL, including on zero detections: a turn that cleared a
        # transient action changes the state the client renders even though
        # nothing was detected. (The legacy app emitted only when something was
        # detected, so a cleared layer never reached the browser.)
        self._events.state(session)

        terminal = session.check_terminal()
        if terminal is not None:
            self._events.game_over(*terminal)
            if self._on_game_over is not None:
                result = self._on_game_over()
                if inspect.isawaitable(result):
                    await result

        logger.info(
            "[timing] session=%s referee=%.2fs detected=%d",
            session.session_id,
            time.perf_counter() - started,
            len(detected),
        )

    async def _detect(self, utterance: str) -> List[str]:
        """Ask the referee LLM which action types this utterance performed.

        Always returns a list — every failure mode scores the turn as empty.
        """
        session = self._session
        user_message = build_referee_payload(
            session.scenario, utterance, session.escalation
        )
        try:
            raw = await asyncio.wait_for(
                self._llm.chat(
                    [{"role": "user", "content": user_message}],
                    self._system_prompt,
                    response_format=self._response_format,
                ),
                # Belt (the client's own aiohttp timeout) and braces: wait_for
                # also covers connector/DNS stalls the request timeout misses.
                timeout=self._timeout_seconds + 1,
            )
            clean = _FENCE_RE.sub("", raw).strip().rstrip("`").strip()
            verdict = RefereeVerdict.model_validate_json(clean)
        # Expected: asyncio.TimeoutError, UpstreamServiceError,
        # aiohttp.ClientError, json.JSONDecodeError, pydantic.ValidationError —
        # caught as bare Exception on purpose, because an unforeseen sixth
        # failure mode must fail open exactly like the five we anticipated.
        except Exception:
            logger.warning(
                "[%s] referee scoring failed; scoring turn as no-detection",
                session.session_id,
                exc_info=True,
            )
            return []

        action_map = session.action_map
        detected: List[str] = []
        for item in verdict.detected_actions:
            if item.type in action_map and item.type not in detected:
                detected.append(item.type)
        return detected
