#!/usr/bin/env python3
"""
Referee eval: run the real referee prompt + schema against the live model.

MANUAL ONLY. This lives outside ``runtime/tests/`` on purpose — it costs money,
needs the network, and is nondeterministic, so CI must never collect it. CI does
lint it (``ruff check runtime``).

    export OPENROUTER_API_KEY=...
    python3 runtime/evals/referee_eval.py [--cases path] [--model m] [--repeat n]

    # The deployed pairing. Needs AWS credentials and AWS_BEDROCK_BASE_URL.
    python3 runtime/evals/referee_eval.py \
        --provider bedrock --model openai.gpt-oss-120b --reasoning medium

Everything under test comes from the production path: the real scenario, the
real ``resources/referee.txt``, ``build_referee_payload`` /
``build_response_format`` / ``build_referee_llm`` / ``parse_referee_verdict``
from ``bridge.referee``. Exits nonzero if any case fails.

Latency is what the ``--provider bedrock`` run is really for. Bedrock ignores
``response_format``, so that run is also the only evidence that the prompt alone
holds the JSON shape — a parse error here is a turn the student would have
silently lost in production, since the referee fails open.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import config  # noqa: E402
from bridge.referee import (  # noqa: E402
    build_referee_llm,
    build_referee_payload,
    build_response_format,
    parse_referee_verdict,
)

CASES_PATH = Path(__file__).resolve().parent / "cases" / "referee.json"


async def run_case(llm, scenario, response_format, system_prompt, case):
    """Score one case; returns (detected, latency_seconds, error)."""
    payload = build_referee_payload(scenario, case["utterance"], case["escalation"])
    started = time.perf_counter()
    try:
        raw = await llm.chat(
            [{"role": "user", "content": payload}],
            system_prompt,
            response_format=response_format,
        )
        verdict = parse_referee_verdict(raw)
        detected = [a.type for a in verdict.detected_actions]
    except Exception as exc:  # the eval reports failures, it does not fail open
        return None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"
    return detected, time.perf_counter() - started, None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--provider", default=config.REFEREE_PROVIDER)
    parser.add_argument("--model", default=config.REFEREE_MODEL)
    parser.add_argument("--reasoning", default=config.REFEREE_REASONING)
    parser.add_argument("--repeat", type=int, default=1, help="runs per case")
    parser.add_argument("--timeout", type=float, default=config.REFEREE_TIMEOUT_SECONDS)
    args = parser.parse_args()

    # build_referee_llm() reads these off the module, so an override has to be
    # written back rather than passed.
    config.REFEREE_PROVIDER = args.provider
    config.REFEREE_MODEL = args.model
    config.REFEREE_REASONING = args.reasoning
    scenario = config.load_scenario()
    system_prompt = config.load_referee_prompt()
    response_format = build_response_format(scenario)
    llm = build_referee_llm(args.timeout)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))

    print(
        f"provider={args.provider} model={args.model} "
        f"reasoning={args.reasoning} cases={len(cases)} repeat={args.repeat}\n"
    )
    failures = 0
    latencies = []
    for case in cases:
        for attempt in range(args.repeat):
            detected, latency, error = await run_case(
                llm, scenario, response_format, system_prompt, case
            )
            latencies.append(latency)
            label = case["name"] + (f" #{attempt + 1}" if args.repeat > 1 else "")
            if error is not None:
                failures += 1
                print(f"ERROR {label} ({latency:.2f}s)\n       {error}")
                continue
            # Order is not part of the contract; the set of types is.
            ok = set(detected) == set(case["expected"])
            print(f"{'PASS ' if ok else 'FAIL '} {label} ({latency:.2f}s)")
            if not ok:
                failures += 1
                missing = sorted(set(case["expected"]) - set(detected))
                extra = sorted(set(detected) - set(case["expected"]))
                print(f"       utterance: {case['utterance']}")
                if missing:
                    print(f"       missing:   {missing}")
                if extra:
                    print(f"       extra:     {extra}")

    total = len(latencies)
    latencies.sort()
    # p95, not just the median: REFEREE_TIMEOUT_SECONDS fails the turn open, so
    # the tail is the number that decides whether the timeout needs raising.
    p95 = latencies[min(total - 1, int(total * 0.95))]
    print(
        f"\n{total - failures}/{total} passed | "
        f"latency median {latencies[total // 2]:.2f}s "
        f"p95 {p95:.2f}s max {latencies[-1]:.2f}s "
        f"(fails open past {args.timeout:.0f}s)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
