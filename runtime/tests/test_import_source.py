"""Lock in that every test run imports the live runtime/ source.

Two mechanisms guarantee it, one assertion each: the root pytest.ini's
``pythonpath = runtime`` (both packages resolve under the repo's runtime/), and
the editable ``-e ./runtime`` in api/requirements.txt (the installed kit is a
link to the source, not a stale copy).
"""

import importlib.metadata
import json
from pathlib import Path

import bridge
import voice_kit

RUNTIME_DIR = Path(__file__).resolve().parent.parent


def test_packages_import_from_repo_runtime():
    for module in (voice_kit, bridge):
        assert Path(module.__file__).resolve().is_relative_to(RUNTIME_DIR), (
            module.__file__
        )


def test_voice_kit_install_is_editable():
    # Not distribution(): with runtime/ first on sys.path it returns the in-tree
    # bridge_voice_kit.egg-info setuptools leaves behind on every build, which
    # carries no install record. Pick the copy pip actually installed.
    installed = [
        dist
        for dist in importlib.metadata.distributions(name="bridge-voice-kit")
        if dist.read_text("INSTALLER")
    ]
    assert len(installed) == 1, "bridge-voice-kit is not pip-installed"
    direct_url = json.loads(installed[0].read_text("direct_url.json"))
    assert direct_url.get("dir_info", {}).get("editable") is True
