#!/usr/bin/env python3
"""
Generate the TypeScript data-channel event types from the pydantic models.

    python3 scripts/gen_event_types.py            # write web/src/voice/gameEvents.gen.ts
    python3 scripts/gen_event_types.py --check    # exit 1 if the committed file drifted

`runtime/bridge/events.py` is the source of truth for the wire contract; the
generated file is committed so the SPA builds without a Python toolchain, and
Backend CI runs `--check` so the two can never diverge silently.

Deliberately hand-rolled with zero third-party deps beyond pydantic itself: no
JSON-Schema round-trip, no node tooling in the backend CI job.
"""

import argparse
import sys
import typing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = REPO_ROOT / "runtime"
OUTPUT_PATH = REPO_ROOT / "web" / "src" / "voice" / "gameEvents.gen.ts"

sys.path.insert(0, str(RUNTIME_DIR))

from bridge.events import EVENT_MODELS  # noqa: E402  — needs the sys.path line above

HEADER = """// GENERATED — do not edit.
//
// Source of truth: runtime/bridge/events.py
// Regenerate:      python3 scripts/gen_event_types.py
//
// The v1 envelope for every message the voice runtime pushes over the WebRTC
// data channel. Backend CI fails if this file drifts from the pydantic models."""


def ts_type(annotation: typing.Any) -> str:
    """Map a pydantic field annotation to its TypeScript spelling."""
    origin = typing.get_origin(annotation)

    if origin is typing.Literal:
        return " | ".join(_ts_literal(arg) for arg in typing.get_args(annotation))
    if origin in (list, set, tuple):
        (item,) = typing.get_args(annotation) or (str,)
        return f"{ts_type(item)}[]"
    if annotation is str:
        return "string"
    if annotation is bool:
        return "boolean"
    if annotation in (int, float):
        return "number"

    raise TypeError(f"No TypeScript mapping for annotation {annotation!r}")


def _ts_literal(value: typing.Any) -> str:
    if isinstance(value, str):
        return f"'{value}'"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    raise TypeError(f"No TypeScript mapping for literal {value!r}")


def render() -> str:
    """Build the full file contents as a string (byte-stable across runs)."""
    blocks = [HEADER]

    for model in EVENT_MODELS:
        doc = (model.__doc__ or "").strip().splitlines()
        lines = [f"/** {doc[0]} */"] if doc else []
        lines.append(f"export interface {model.__name__} {{")
        for name, field in model.model_fields.items():
            lines.append(f"  {name}: {ts_type(field.annotation)}")
        lines.append("}")
        blocks.append("\n".join(lines))

    union = "\n  | ".join(model.__name__ for model in EVENT_MODELS)
    blocks.append(f"export type GameEvent =\n  | {union}")

    return "\n\n".join(blocks) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed file matches the models instead of writing it",
    )
    args = parser.parse_args()

    generated = render()
    rel = OUTPUT_PATH.relative_to(REPO_ROOT)

    if args.check:
        current = OUTPUT_PATH.read_text() if OUTPUT_PATH.exists() else ""
        if current != generated:
            print(
                f"{rel} is out of date with runtime/bridge/events.py.\n"
                "Regenerate it: python3 scripts/gen_event_types.py",
                file=sys.stderr,
            )
            return 1
        print(f"{rel} is up to date.")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(generated)
    print(f"Wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
