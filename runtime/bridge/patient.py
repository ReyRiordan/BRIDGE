"""
The patient agent's inputs: system prompt, voice, and per-turn context.

The prompt here is a PLACEHOLDER — a one-liner that keeps the pipeline honest
end to end. The real patient persona (the case file, the locked-information
rules, the escalation-conditioned behaviour) is [Rewrite E], which replaces the
body of ``build_patient_prompt`` and nothing else: the seam, the voice mapping
and the turn context are settled here.
"""

from voice_kit.types import VoiceConfig

PLACEHOLDER_PROMPT = (
    "You are a 22-year-old male patient with autism spectrum disorder in a busy "
    "emergency department, agitated after a failed IV attempt. Reply in one or "
    "two short spoken sentences."
)


def build_patient_prompt(scenario: dict) -> str:
    """The patient agent's system prompt. Body replaced by [Rewrite E]."""
    return PLACEHOLDER_PROMPT


def build_voice_config(scenario: dict) -> VoiceConfig:
    """Map the scenario's ``speech`` block to the kit's TTS voice config."""
    speech = scenario["speech"]
    return VoiceConfig(
        provider=speech["provider"],
        voice=speech["voice"],
        model=speech.get("model"),
        speed=float(speech.get("speed", 1.0)),
    )


def turn_context(session) -> str:
    """The ephemeral per-turn marker injected just before the student's message."""
    return f"[CURRENT ESCALATION: {session.escalation}/{session.max_escalation}]"
