"""
The patient agent's inputs: system prompt, voice, and per-turn context.

The prompt is assembled from two files — ``resources/patient.txt`` (the persona
and its escalation-behaviour table, carrying a ``{patient_name}`` placeholder)
and ``resources/patient.json`` (the case file) — joined by a rendered
``PATIENT CASE DETAILS`` section.

``locked_information`` is deliberately NEVER rendered. History-taking is out of
scope for the current sim; the case file keeps the items as seed data for that
future feature, and withholding them from the prompt entirely is a stronger
guarantee than any instruction — the model cannot leak what it never sees.
"""

from typing import Optional

from voice_kit.types import VoiceConfig

from . import config


def build_patient_prompt(
    scenario: dict,
    template: Optional[str] = None,
    case: Optional[dict] = None,
) -> str:
    """The patient agent's system prompt: persona template + case details.

    ``template``/``case`` override the configured files (tests, evals).
    """
    if template is None:
        template = config.load_patient_prompt_template()
    if case is None:
        case = config.load_patient_case()

    demo = case["demographics"]
    # str.replace, not str.format: the template body is curly-brace-heavy.
    body = template.replace("{patient_name}", demo["name"])

    parts = [
        "\n\n=== PATIENT CASE DETAILS ===\n",
        (
            "<demographics>\n"
            f"name: {demo['name']}\n"
            f"date_of_birth: {demo['date_of_birth']}\n"
            f"sex: {demo['sex']}\n"
            f"gender: {demo['gender']}\n"
            f"background: {demo['background']}\n"
            "</demographics>\n"
        ),
    ]
    if "behavior" in case:
        parts.append(f"<behavior>\n{case['behavior']}\n</behavior>\n")
    parts.append(f"<chief_concern>\n{case['chief_concern']}\n</chief_concern>\n")
    free_items = "\n".join(f"- {item}" for item in case["free_information"])
    parts.append(
        "<free_information>\n"
        "Information you may volunteer or mention naturally:\n"
        f"{free_items}\n"
        "</free_information>"
    )
    # No <locked_information> block — see the module docstring.
    return body + "\n".join(parts)


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
