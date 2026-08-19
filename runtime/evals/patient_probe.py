#!/usr/bin/env python3
"""
Patient probe: does terseness track escalation, and is history deflected?

MANUAL ONLY, for the same reasons as ``referee_eval.py``. It asserts nothing —
it prints the patient's replies at a ladder of escalation levels so you can read
them side by side. A 30-second check before a manual voice run.

    export OPENROUTER_API_KEY=...
    python3 runtime/evals/patient_probe.py [--model m]

The prompt is the real one (``build_patient_prompt``), and the per-turn marker
is the real one (``turn_context``'s format), so what the model sees here is what
it sees in the pipeline.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import config  # noqa: E402
from bridge.patient import build_patient_prompt  # noqa: E402
from voice_kit.config import settings  # noqa: E402
from voice_kit.providers.llm import OpenRouterChat  # noqa: E402

ESCALATIONS = [10, 8, 5, 2, 0]

PROBES = [
    ("de-escalation", "Hey Jordan, I'm going to dim the lights for you. Is that okay?"),
    (
        "history question",
        "When exactly did the pain start, and have you had it before?",
    ),
]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="anthropic/claude-haiku-4.5")
    args = parser.parse_args()

    scenario = config.load_scenario()
    system_prompt = build_patient_prompt(scenario)
    max_escalation = scenario["point_bar"]["max"]
    llm = OpenRouterChat(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        model=args.model,
        reasoning_effort="none",
    )

    for label, line in PROBES:
        print(f"\n=== {label}: {line}\n")
        for escalation in ESCALATIONS:
            marker = f"[CURRENT ESCALATION: {escalation}/{max_escalation}]"
            reply = await llm.chat(
                [{"role": "user", "content": f"{marker}\n{line}"}], system_prompt
            )
            print(f"  {escalation:>2}: {reply.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
