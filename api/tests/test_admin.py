"""
The results API: login, the bearer token (tamper, expiry, rotation), the gated
list and detail endpoints, display-status derivation and Decimal handling.
"""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import admin, main, sessions
from api.session_store import MemorySessionStore

PASSWORD = "correct-horse-battery-staple"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv(admin.PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr(admin.settings, "bridge_local", False)


@pytest.fixture
def penalties(monkeypatch):
    calls = []

    async def record():
        calls.append(admin.WRONG_PASSWORD_DELAY_S)

    monkeypatch.setattr(admin, "_wrong_password_penalty", record)
    return calls


@pytest.fixture
def memory(monkeypatch):
    store = MemorySessionStore()
    monkeypatch.setattr(sessions, "store", lambda: store)
    return store


@pytest.fixture
def client():
    return TestClient(main.app)


def auth(token=None):
    return {"Authorization": f"Bearer {token or admin.issue_token()[0]}"}


def iso(dt):
    return dt.isoformat()


# --- login -------------------------------------------------------------------


def test_login_with_the_right_password_issues_a_12h_token(client, penalties):
    res = client.post("/admin/login", json={"password": PASSWORD})
    assert res.status_code == 200
    body = res.json()
    assert admin.verify_token(body["token"])
    assert abs(body["expires_at"] - (datetime.now().timestamp() + 12 * 3600)) < 5
    assert penalties == []


def test_a_wrong_password_is_401_after_the_damper(client, penalties):
    res = client.post("/admin/login", json={"password": "nope"})
    assert res.status_code == 401
    assert penalties == [admin.WRONG_PASSWORD_DELAY_S]
    assert admin.WRONG_PASSWORD_DELAY_S >= 1


def test_login_is_503_when_unconfigured(client, monkeypatch, penalties):
    monkeypatch.delenv(admin.PASSWORD_ENV)
    res = client.post("/admin/login", json={"password": "local"})
    assert res.status_code == 503
    assert res.json()["detail"] == "admin not configured"


def test_bridge_local_accepts_the_local_default(client, monkeypatch, penalties):
    monkeypatch.delenv(admin.PASSWORD_ENV)
    monkeypatch.setattr(admin.settings, "bridge_local", True)
    res = client.post("/admin/login", json={"password": "local"})
    assert res.status_code == 200
    assert admin.verify_token(res.json()["token"])


def test_a_set_password_overrides_the_local_default(client, monkeypatch, penalties):
    monkeypatch.setattr(admin.settings, "bridge_local", True)
    assert client.post("/admin/login", json={"password": "local"}).status_code == 401


# --- the token -----------------------------------------------------------------


def test_token_round_trip_and_expiry():
    token, exp = admin.issue_token(now=1_000_000)
    assert exp == 1_000_000 + admin.TOKEN_LIFETIME_S
    assert admin.verify_token(token, now=exp - 1)
    assert not admin.verify_token(token, now=exp)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p, s: admin._b64(b'{"exp":9999999999}') + "." + s,  # forged payload
        lambda p, s: f"{p}.{s[:-1]}{'B' if s[-1] == 'A' else 'A'}",  # forged sig
        lambda p, s: f"{p}{s}",  # no separator
        lambda p, s: f"{p}.{s}.x",  # extra part
        lambda p, s: "",
    ],
)
def test_a_tampered_token_is_rejected(mutate):
    payload, signature = admin.issue_token()[0].split(".")
    assert not admin.verify_token(mutate(payload, signature))


def test_a_signed_payload_without_an_int_exp_is_rejected():
    payload = admin._b64(b'{"exp":"never"}')
    token = f"{payload}.{admin._signature(PASSWORD, payload)}"
    assert not admin.verify_token(token)


def test_rotating_the_password_invalidates_old_tokens(monkeypatch):
    token, _ = admin.issue_token()
    monkeypatch.setenv(admin.PASSWORD_ENV, "a-new-password")
    assert not admin.verify_token(token)
    assert admin.verify_token(admin.issue_token()[0])


def test_no_token_verifies_while_unconfigured(monkeypatch):
    token, _ = admin.issue_token()
    monkeypatch.delenv(admin.PASSWORD_ENV)
    assert not admin.verify_token(token)


# --- gating ----------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/admin/sessions", "/admin/sessions/x--y"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer junk"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
    ],
)
def test_data_endpoints_are_401_without_a_valid_token(client, memory, path, headers):
    res = client.get(path, headers=headers)
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


def test_an_expired_token_is_401(client, memory):
    token, _ = admin.issue_token(now=datetime.now().timestamp() - 13 * 3600)
    assert client.get("/admin/sessions", headers=auth(token)).status_code == 401


# --- list + detail ---------------------------------------------------------------


def make_item(session_id, created_at, **extra):
    return {
        "session_id": session_id,
        "kind": "session",
        "created_at": created_at,
        "netid": "test01",
        "scenario_id": "scenario_1",
        "scenario_title": "Scenario One",
        "mode": "voice",
        "user_agent": "UA/1",
        "status": "success",
        "end_reason": "goal",
        "reason": None,
        "start_escalation": 5,
        "escalation": 0,
        "max_escalation": 10,
        "goal": 0,
        "time_limit_s": 300,
        "helpful_count": 3,
        "escalating_count": 1,
        "turns": [{"role": "student", "t": 1.5, "content": "hi", "actions": []}],
        "timeline": [{"t": 0, "escalation": 5, "cause": "start"}],
        **extra,
    }


def test_list_is_newest_first_and_carries_only_summary_fields(client, memory):
    old = make_item("scenario_1--a", "2026-11-01T10:00:00+00:00")
    new = make_item("scenario_1--b", "2026-11-02T10:00:00+00:00", mode="text")
    memory.put(old)
    memory.put(new)

    res = client.get("/admin/sessions", headers=auth())
    assert res.status_code == 200
    rows = res.json()["sessions"]
    assert [r["session_id"] for r in rows] == ["scenario_1--b", "scenario_1--a"]
    assert rows[0]["mode"] == "text"
    assert rows[0]["display_status"] == "success"
    for row in rows:
        assert set(row) <= set(admin.SUMMARY_FIELDS) | {"display_status"}
        assert "turns" not in row and "timeline" not in row and "user_agent" not in row


def test_detail_returns_the_whole_item(client, memory):
    item = make_item("scenario_1--a", "2026-11-01T10:00:00+00:00")
    memory.put(item)
    res = client.get("/admin/sessions/scenario_1--a", headers=auth())
    assert res.status_code == 200
    assert res.json() == {**item, "display_status": "success"}


@pytest.mark.parametrize("session_id", ["scenario_1--missing", "..%2Fetc"])
def test_detail_is_404_for_an_unknown_id(client, memory, session_id):
    res = client.get(f"/admin/sessions/{session_id}", headers=auth())
    assert res.status_code == 404


def test_the_file_store_404s_an_unsafe_id(client, monkeypatch, tmp_path):
    from api.session_store import FileSessionStore

    monkeypatch.setattr(sessions, "store", lambda: FileSessionStore(tmp_path))
    res = client.get("/admin/sessions/a.b", headers=auth())
    assert res.status_code == 404


class DecimalStore(MemorySessionStore):
    """What a raw boto3 resource hands back: every number a Decimal."""

    def get(self, session_id):
        return {
            "session_id": session_id,
            "created_at": "2026-11-01T10:00:00+00:00",
            "status": "fail",
            "escalation": Decimal("10"),
            "duration_s": Decimal("212.4"),
            "turns": [
                {"t": Decimal("12.3"), "actions": [{"point_change": Decimal("-3")}]}
            ],
        }

    def list_summaries(self):
        return [self.get("scenario_1--a")]


def test_decimals_serialise_as_json_numbers(client, monkeypatch):
    monkeypatch.setattr(sessions, "store", lambda: DecimalStore())
    detail = client.get("/admin/sessions/scenario_1--a", headers=auth()).json()
    assert detail["escalation"] == 10 and isinstance(detail["escalation"], int)
    assert detail["duration_s"] == 212.4
    assert detail["turns"][0]["t"] == 12.3
    assert detail["turns"][0]["actions"][0]["point_change"] == -3

    (row,) = client.get("/admin/sessions", headers=auth()).json()["sessions"]
    assert row["duration_s"] == 212.4 and row["escalation"] == 10


def test_summary_fields_match_the_gsi_projection():
    ts = (Path(__file__).resolve().parents[2] / "amplify" / "constants.ts").read_text()
    block = re.search(r"SESSION_SUMMARY_ATTRIBUTES = \[(.*?)\]", ts, re.S).group(1)
    projected = re.findall(r"'([a-z_]+)'", block)
    keys = {"session_id", "kind", "created_at"}
    assert set(admin.SUMMARY_FIELDS) == keys | set(projected)


# --- display status ------------------------------------------------------------


def started(minutes_ago, **extra):
    return {
        "status": "active",
        "started_at": iso(NOW - timedelta(minutes=minutes_ago)),
        "time_limit_s": 300,
        **extra,
    }


@pytest.mark.parametrize(
    "item, expected",
    [
        # active: abandoned only past started_at + time limit + 5 min
        (started(9), "active"),
        (started(10.1), "abandoned"),
        (started(10.1, time_limit_s=600), "active"),
        (started(15.1, time_limit_s=600), "abandoned"),
        # active with no started_at falls back to created_at
        (
            {"status": "active", "created_at": iso(NOW - timedelta(hours=2))},
            "abandoned",
        ),
        ({"status": "active"}, "active"),
        # started: never connected after an hour
        (
            {"status": "started", "created_at": iso(NOW - timedelta(minutes=59))},
            "started",
        ),
        (
            {"status": "started", "created_at": iso(NOW - timedelta(minutes=61))},
            "never_connected",
        ),
        ({"status": "started", "created_at": "not a date"}, "started"),
        # a naive timestamp is read as UTC
        ({"status": "started", "created_at": "2026-11-16T10:00:00"}, "never_connected"),
        # terminal statuses pass through, however old
        ({"status": "success", "created_at": iso(NOW - timedelta(days=9))}, "success"),
        ({"status": "fail", "started_at": iso(NOW - timedelta(days=9))}, "fail"),
        ({"status": "abandoned"}, "abandoned"),
    ],
)
def test_display_status(item, expected):
    assert admin.display_status(item, NOW) == expected
