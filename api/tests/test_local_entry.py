"""
The local uvicorn entry point: same app object, flag set before the import that
builds the voice router.
"""

import importlib
import os
import sys


def _reload_local():
    """Import api.local from scratch so the module-level env write re-runs."""
    sys.modules.pop("api.local", None)
    return importlib.import_module("api.local")


def test_local_sets_the_flag_and_reuses_the_same_app(monkeypatch):
    monkeypatch.delenv("BRIDGE_LOCAL", raising=False)

    local = _reload_local()

    assert os.environ["BRIDGE_LOCAL"] == "1"
    # Not a second app: the local loop must exercise the deployed wiring.
    from api import main

    assert local.app is main.app


def test_an_explicit_flag_value_wins(monkeypatch):
    # setdefault, not assignment — `BRIDGE_LOCAL=0 uvicorn api.local:app` points
    # a local control plane at the deployed runtime.
    monkeypatch.setenv("BRIDGE_LOCAL", "0")

    _reload_local()

    assert os.environ["BRIDGE_LOCAL"] == "0"
