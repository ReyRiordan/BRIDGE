"""
Control-plane app tests: the deploy contract infra depends on (the ASGI `app`
the image serves under LWA plus the Mangum `handler` zip fallback, /health,
in-app CORS), the /scenarios list and /scenario/{id} whitelist, and the mounted voice router — including the 502-with-CORS acceptance path (kit gotcha #22).
"""

import json

import pytest
from fastapi.testclient import TestClient
from voice_kit import UpstreamServiceError

from api import main, scenario

ORIGIN = "https://app.example"


@pytest.fixture
def client(monkeypatch):
    # CORSMiddleware read ALLOWED_ORIGINS at import time; patch the installed
    # middleware's origin list so Origin-bearing tests get real CORS behavior.
    for m in main.app.user_middleware:
        if m.cls.__name__ == "CORSMiddleware":
            m.kwargs["allow_origins"] = [ORIGIN]
    main.app.middleware_stack = None  # force rebuild with the patched origins
    return TestClient(main.app)


class FakeInvoker:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.end_calls = []

    async def signal(self, session_id, runtime_session_id, sdp, type="offer"):
        if self.fail:
            raise UpstreamServiceError(
                message="The voice service could not be reached."
            )
        return {"sdp": "answer-sdp", "type": "answer"}

    async def end(self, session_id, runtime_session_id):
        self.end_calls.append((session_id, runtime_session_id))
        return {"status": "ended"}


# --- deploy contract ---------------------------------------------------------


def test_handler_is_the_zip_packaging_fallback():
    # Production runs `uvicorn api.main:app` under LWA, but the Mangum handler
    # stays as the zip fallback — keep the name importable.
    assert callable(main.handler)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "scenario_loaded": True}


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


# --- /scenarios + /scenario/{id} --------------------------------------------

SCENARIO_IDS = ["scenario_1", "scenario_2", "scenario_3"]
WHITELIST = {
    "id",
    "title",
    "intro",
    "goal",
    "intro_image",
    "environment",
    "patient_frame_ext",
    "inactivity",
    "actions",
    "point_bar",
    "time_limit",
}


def _source(scenario_id):
    path = scenario.DEFAULT_SCENARIOS_DIR / scenario_id / "scenario.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_scenarios_lists_every_scenario_for_the_picker(client):
    body = client.get("/scenarios").json()
    assert [s["id"] for s in body] == SCENARIO_IDS
    for card in body:
        source = _source(card["id"])
        assert card == {
            "id": source["id"],
            "title": source["title"],
            "intro_image": source["intro_image"],
        }


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_scenario_serves_the_whitelist(client, scenario_id):
    source = _source(scenario_id)
    body = client.get(f"/scenario/{scenario_id}").json()

    assert set(body) == WHITELIST
    for key in ("id", "title", "intro", "goal", "intro_image", "actions"):
        assert body[key] == source[key]
    assert body["point_bar"] == source["point_bar"]
    assert body["time_limit"] == source["time_limit"]
    # Server-only keys never leave: `speech` is the runtime's business.
    assert "speech" not in body


def test_optional_keys_default_for_scenario_1(client):
    body = client.get("/scenario/scenario_1").json()
    assert body["environment"] is None
    assert body["inactivity"] is None
    assert body["patient_frame_ext"] == "png"


def test_optional_keys_come_through_for_scenario_3(client):
    body = client.get("/scenario/scenario_3").json()
    assert body["environment"] == "environment.webp"
    assert body["inactivity"] == {"seconds": 120, "point_change": 1}
    assert body["patient_frame_ext"] == "webp"
    end = next(a for a in body["actions"] if a.get("ends_simulation"))
    assert end["debrief"]


def test_unknown_scenario_is_a_404(client):
    assert client.get("/scenario/scenario_9").status_code == 404
    # The old single-scenario route is gone.
    assert client.get("/scenario").status_code == 404


def test_load_all_rejects_an_id_that_does_not_match_its_folder(tmp_path):
    (tmp_path / "scenario_x").mkdir()
    (tmp_path / "scenario_x" / "scenario.json").write_text('{"id": "other"}')
    with pytest.raises(ValueError, match="folder"):
        scenario._load_all(tmp_path)


def test_load_all_fails_loudly_on_an_empty_bundle(tmp_path):
    with pytest.raises(FileNotFoundError):
        scenario._load_all(tmp_path)


# --- voice router ------------------------------------------------------------


def _flat_routes():
    def walk(routes):
        for r in routes:
            # FastAPI 0.141 wraps included routers; unwrap to their routes.
            inner = getattr(r, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes)
            else:
                yield r

    return list(walk(main.app.routes))


def _voice_paths():
    return {r.path for r in _flat_routes() if hasattr(r, "path")}


def test_the_three_voice_routes_are_mounted():
    assert {
        "/voice/{session_id}/start",
        "/voice/{session_id}/signal",
        "/voice/{session_id}/end",
    } <= _voice_paths()


def test_start_returns_a_33_plus_char_runtime_session_id(client, monkeypatch):
    from voice_kit.control_plane import router as router_module

    monkeypatch.setattr(router_module, "fetch_ice_servers", lambda: [])
    body = client.post("/voice/sess-1/start").json()

    assert len(body["runtime_session_id"]) >= 33
    assert body["session_id"] == "sess-1"


def _swap_invoker(monkeypatch, invoker):
    # The invoker is captured in the router's closure; patch the closure cell
    # the handlers read.
    for route in _flat_routes():
        fn = getattr(route, "endpoint", None)
        if fn is None or fn.__closure__ is None:
            continue
        for name, cell in zip(fn.__code__.co_freevars, fn.__closure__):
            if name == "invoker":
                cell.cell_contents = invoker


def test_upstream_failure_is_a_structured_502_with_cors_headers(client, monkeypatch):
    # THE acceptance test for kit gotcha #22: a forced upstream failure must
    # reach the browser as a CORS-bearing 502, never a CORS-less 500.
    _swap_invoker(monkeypatch, FakeInvoker(fail=True))

    resp = client.post(
        "/voice/sess-1/signal",
        json={"runtime_session_id": "r" * 33, "sdp": "offer"},
        headers={"Origin": ORIGIN},
    )

    assert resp.status_code == 502
    error = resp.json()["error"]
    assert error["code"] == "UPSTREAM_SERVICE_ERROR"
    assert resp.headers.get("access-control-allow-origin") == ORIGIN


def test_end_relays_teardown_through_the_invoker(client, monkeypatch):
    invoker = FakeInvoker()
    _swap_invoker(monkeypatch, invoker)

    resp = client.post("/voice/sess-1/end", json={"runtime_session_id": "r" * 33})

    assert resp.status_code == 200
    assert resp.json()["transcript"] == []  # no session store by design
    assert invoker.end_calls == [("sess-1", "r" * 33)]
