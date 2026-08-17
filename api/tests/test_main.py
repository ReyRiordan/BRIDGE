"""
Tests for the [Rewrite C] placeholder app. They pin the deploy contract the
infra depends on (a Mangum `handler`, a working /health, CORS owned in-app),
not the placeholder's own shape.
"""

import json

import pytest
from fastapi.testclient import TestClient

from api import main


@pytest.fixture
def client():
    return TestClient(main.app)


def test_handler_is_the_lambda_entry_point():
    # Infra's handler string is "api.main.handler" — keep the name.
    assert callable(main.handler)


def test_health_reports_the_bundled_scenario(client):
    body = client.get("/health").json()
    assert body == {"status": "ok", "scenario_loaded": True}


def test_health_survives_a_missing_scenario(client, monkeypatch, tmp_path):
    monkeypatch.setenv("SCENARIO_PATH", str(tmp_path / "nope.json"))
    body = client.get("/health").json()
    assert body == {"status": "ok", "scenario_loaded": False}


def test_default_scenario_path_resolves_next_to_the_package():
    # Bundling copies resources/ alongside api/; this is that layout.
    assert main.DEFAULT_SCENARIO_PATH.exists()
    assert json.loads(main.DEFAULT_SCENARIO_PATH.read_text())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("http://localhost:5173", ["http://localhost:5173"]),
        (" a.example , b.example ", ["a.example", "b.example"]),
        ("a.example,,", ["a.example"]),
    ],
)
def test_allowed_origins_parses_the_infra_env(monkeypatch, raw, expected):
    # The Function URL sets no CORS config, so this list is the only thing
    # standing between the SPA and a CORS failure.
    monkeypatch.setenv("ALLOWED_ORIGINS", raw)
    assert main.allowed_origins() == expected


def test_cors_is_not_wide_open(client):
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.headers.get("access-control-allow-origin") != "*"
