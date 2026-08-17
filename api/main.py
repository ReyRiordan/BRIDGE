"""
Control-plane Lambda entry point — PLACEHOLDER for [Rewrite C].

Today this is the smallest app that proves the deploy path end to end: the
bundle installs, Mangum adapts it to the Function URL, CORS lets the SPA in,
and `resources/scenario_1.json` really is inside the package. [Rewrite C]
replaces this module wholesale with the real control plane (`/scenario` + the
`voice_kit` signaling router) — deliberately no voice_kit import here, since how
`api/` consumes the package is that issue's decision.

CORS is owned here, not by the Function URL: configuring both duplicates the
response headers and browsers reject them.
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

# Bundling copies resources/ next to api/ inside the package, so the default
# resolves relative to this file — infra deliberately does NOT set SCENARIO_PATH
# on the Lambda (that var carries the *container's* /app/resources path, which
# does not exist here). The override exists for local runs.
DEFAULT_SCENARIO_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "scenario_1.json"
)


def allowed_origins() -> list[str]:
    """Browser origins from the comma-separated ALLOWED_ORIGINS set by infra."""
    return [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]


app = FastAPI(title="BRIDGE control plane")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _scenario_path() -> Path:
    configured = os.getenv("SCENARIO_PATH")
    return Path(configured) if configured else DEFAULT_SCENARIO_PATH


@app.get("/health")
def health() -> dict:
    """Liveness + a bundling check: can we actually read the scenario config?"""
    path = _scenario_path()
    try:
        scenario = json.loads(path.read_text())
        scenario_loaded = bool(scenario)
    except (OSError, ValueError):
        scenario_loaded = False
    return {"status": "ok", "scenario_loaded": scenario_loaded}


handler = Mangum(app)
