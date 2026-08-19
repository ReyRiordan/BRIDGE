"""
Local-dev entry point for the control plane: ``uvicorn api.local:app --port 8000``.

The only difference from ``api.main`` is that ``BRIDGE_LOCAL`` is in the
environment before the app is imported. That ordering is load-bearing:
``create_voice_router`` resolves ``invoker or get_invoker()`` when the router is
BUILT, and ``api/main.py`` builds it at import — so setting the flag afterwards
would leave a control plane wired to ``AgentCoreInvoker`` and pointed at AWS.

``setdefault``, not assignment: an explicit ``BRIDGE_LOCAL=0`` in the
environment still wins, so this module can also be run against a deployed
backend when that is what you actually want.

The deployed Lambda imports ``api.main`` directly (``api.main.handler``) and
never reaches this module.
"""

import os

os.environ.setdefault("BRIDGE_LOCAL", "1")

from api.main import app  # noqa: E402  — the flag must precede this import

__all__ = ["app"]
