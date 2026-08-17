"""
Scaffold smoke test — the control-plane app itself lands in [Rewrite C].

Keep at least one test here: pytest exits 5 ("no tests collected"), which fails
the Backend CI test job.
"""

import api


def test_api_package_imports():
    assert api.__doc__
