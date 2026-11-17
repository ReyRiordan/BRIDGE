"""
POST /sessions: NetID normalisation, validation, the stored item, and the
Dynamo request shapes behind the store (stubbed, no network).
"""

import json
from decimal import Decimal

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from api import main, sessions
from api.session_store import (
    DynamoSessionStore,
    FileSessionStore,
    MemorySessionStore,
    get_session_store,
)


@pytest.fixture
def memory(monkeypatch):
    store = MemorySessionStore()
    monkeypatch.setattr(sessions, "store", lambda: store)
    return store


@pytest.fixture
def client():
    return TestClient(main.app)


def create(client, **overrides):
    body = {"netid": "abc123", "scenario_id": "scenario_3", "mode": "voice"}
    return client.post(
        "/sessions", json={**body, **overrides}, headers={"User-Agent": "UA/1"}
    )


def test_create_returns_a_prefixed_id_and_stores_the_item(client, memory):
    res = create(client)
    assert res.status_code == 200
    session_id = res.json()["session_id"]
    assert session_id.startswith("scenario_3--")

    item = memory.items[session_id]
    assert item["kind"] == "session"
    assert item["netid"] == "abc123"
    assert item["mode"] == "voice"
    assert item["status"] == "started"
    assert item["scenario_title"] == main.scenario._scenarios["scenario_3"]["title"]
    assert item["start_escalation"] == 5
    assert item["time_limit_s"] == 300
    assert item["user_agent"] == "UA/1"
    assert item["turns"] == [] and item["timeline"] == []
    assert item["created_at"].endswith("+00:00")


def test_each_create_issues_a_new_id(client, memory):
    assert create(client).json() != create(client).json()


def test_user_agent_is_truncated(client, memory):
    res = client.post(
        "/sessions",
        json={"netid": "abc123", "scenario_id": "scenario_1", "mode": "text"},
        headers={"User-Agent": "x" * 1000},
    )
    (item,) = memory.items.values()
    assert res.status_code == 200
    assert len(item["user_agent"]) == 256
    assert item["mode"] == "text"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ABC123", "abc123"),
        ("abc123@rutgers.edu", "abc123"),
        (" jd12 ", "jd12"),
        ("jd", "jd"),
    ],
)
def test_normalize_netid(raw, expected):
    assert sessions.normalize_netid(raw) == expected


@pytest.mark.parametrize(
    "raw", ["a", "123", "ab-12", "ab12cd", "", "a" * 21, "@rutgers.edu"]
)
def test_normalize_netid_rejects(raw):
    assert sessions.normalize_netid(raw) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"netid": "a"},
        {"netid": "123"},
        {"netid": "ab-12"},
        {"scenario_id": "scenario_9"},
        {"mode": "video"},
    ],
)
def test_bad_input_is_422_and_stores_nothing(client, memory, overrides):
    assert create(client, **overrides).status_code == 422
    assert memory.items == {}


def test_the_stored_netid_is_normalised(client, memory):
    create(client, netid=" ABC123@rutgers.edu ")
    (item,) = memory.items.values()
    assert item["netid"] == "abc123"


# --- stores -------------------------------------------------------------------


def a_dynamo_store():
    resource = boto3.resource(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    )
    store = DynamoSessionStore("BridgeSessions", resource=resource)
    return store, Stubber(store._table.meta.client)


def test_dynamo_put_shape():
    store, stubber = a_dynamo_store()
    stubber.add_response(
        "put_item",
        {},
        {
            "TableName": "BridgeSessions",
            # The resource layer: Decimal for floats, plain JSON otherwise.
            "Item": {
                "session_id": "scenario_1--a",
                "kind": "session",
                "duration_s": Decimal("1.5"),
                "turns": [],
                "end_reason": None,
            },
        },
    )
    with stubber:
        store.put(
            {
                "session_id": "scenario_1--a",
                "kind": "session",
                "duration_s": 1.5,
                "turns": [],
                "end_reason": None,
            }
        )
    stubber.assert_no_pending_responses()


def test_dynamo_query_reads_every_page_of_the_index():
    store, stubber = a_dynamo_store()
    expected = {
        "TableName": "BridgeSessions",
        "IndexName": "by_created",
        "KeyConditionExpression": Key("kind").eq("session"),
        "ScanIndexForward": False,
    }
    stubber.add_response(
        "query",
        {
            "Items": [{"session_id": {"S": "b"}, "duration_s": {"N": "2.5"}}],
            "LastEvaluatedKey": {"session_id": {"S": "b"}},
        },
        expected,
    )
    stubber.add_response(
        "query",
        {"Items": [{"session_id": {"S": "a"}, "escalation": {"N": "3"}}]},
        {**expected, "ExclusiveStartKey": {"session_id": "b"}},
    )
    with stubber:
        items = store.list_summaries()
    assert items == [
        {"session_id": "b", "duration_s": 2.5},
        {"session_id": "a", "escalation": 3},
    ]


def test_dynamo_get_converts_decimals():
    store, stubber = a_dynamo_store()
    stubber.add_response(
        "get_item",
        {"Item": {"session_id": {"S": "a"}, "t": {"N": "1.5"}}},
        {"TableName": "BridgeSessions", "Key": {"session_id": "a"}},
    )
    stubber.add_response(
        "get_item", {}, {"TableName": "BridgeSessions", "Key": {"session_id": "z"}}
    )
    with stubber:
        assert store.get("a") == {"session_id": "a", "t": 1.5}
        assert store.get("z") is None


def test_file_store_round_trip(tmp_path):
    store = FileSessionStore(tmp_path / "sessions")
    store.put({"session_id": "s1", "created_at": "2026-01-01"})
    store.put({"session_id": "s2", "created_at": "2026-02-01"})
    assert store.get("s1") == {"session_id": "s1", "created_at": "2026-01-01"}
    assert store.get("nope") is None
    assert [i["session_id"] for i in store.list_summaries()] == ["s2", "s1"]
    assert json.loads((tmp_path / "sessions" / "s1.json").read_text())


def test_file_store_refuses_a_path_like_id(tmp_path):
    with pytest.raises(ValueError):
        FileSessionStore(tmp_path).get("../x")


def test_store_selection(monkeypatch, tmp_path):
    from voice_kit.config import settings

    monkeypatch.setattr(settings, "bridge_local", True)
    monkeypatch.setenv("SESSIONS_LOCAL_DIR", str(tmp_path))
    assert isinstance(get_session_store(), FileSessionStore)

    monkeypatch.setattr(settings, "bridge_local", False)
    monkeypatch.delenv("SESSIONS_TABLE", raising=False)
    assert isinstance(get_session_store(), MemorySessionStore)
