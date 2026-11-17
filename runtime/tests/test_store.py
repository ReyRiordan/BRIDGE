"""
The runtime's session-record backends: the Dynamo request shape (stubbed, no
network), the local file store, and which one ``get_session_table`` picks.
"""

import json
from decimal import Decimal

import boto3
import pytest
from botocore.stub import Stubber
from bridge import store
from bridge.store import (
    DynamoSessionTable,
    FileSessionTable,
    NullSessionTable,
    build_update_expression,
    get_session_table,
)
from voice_kit.config import settings

UPDATE = {
    "set": {"status": "active", "duration_s": 12.5},
    "append": {"turns": [{"role": "patient", "t": 1.5, "content": "hi"}]},
    "add": {"helpful_count": 1},
}


def a_dynamo_table():
    resource = boto3.resource(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    )
    table = DynamoSessionTable("BridgeSessions", resource=resource)
    return table, Stubber(table._table.meta.client)


def test_dynamo_update_is_one_conditional_update_item():
    table, stubber = a_dynamo_table()
    stubber.add_response(
        "update_item",
        {},
        {
            "TableName": "BridgeSessions",
            "Key": {"session_id": "scenario_1--abc"},
            "UpdateExpression": (
                "SET #s0 = :s0, #s1 = :s1, "
                "#a0 = list_append(if_not_exists(#a0, :empty), :a0) "
                "ADD #c0 :c0"
            ),
            "ConditionExpression": "attribute_exists(session_id)",
            "ExpressionAttributeNames": {
                "#s0": "status",
                "#s1": "duration_s",
                "#a0": "turns",
                "#c0": "helpful_count",
            },
            "ExpressionAttributeValues": {
                ":s0": "active",
                ":s1": Decimal("12.5"),
                ":empty": [],
                ":a0": [{"role": "patient", "t": Decimal("1.5"), "content": "hi"}],
                ":c0": 1,
            },
        },
    )
    with stubber:
        table.update("scenario_1--abc", UPDATE)
    stubber.assert_no_pending_responses()


def test_dynamo_condition_failure_raises_for_the_recorder_to_log():
    table, stubber = a_dynamo_table()
    stubber.add_client_error("update_item", "ConditionalCheckFailedException")
    with stubber, pytest.raises(Exception, match="ConditionalCheckFailed"):
        table.update("scenario_1--unknown", {"set": {"status": "active"}})


def test_dynamo_skips_an_empty_update():
    table, stubber = a_dynamo_table()
    with stubber:  # no response queued: any call would raise
        table.update("s", {"set": {}, "append": {"turns": []}, "add": {}})


def test_build_update_expression_omits_empty_appends():
    expr = build_update_expression({"set": {"a": 1}, "append": {"timeline": []}})
    assert expr["UpdateExpression"] == "SET #s0 = :s0"
    assert ":empty" not in expr["ExpressionAttributeValues"]


def test_file_store_round_trip(tmp_path):
    (tmp_path / "s1.json").write_text(
        json.dumps({"session_id": "s1", "turns": [], "helpful_count": 0})
    )
    t = FileSessionTable(tmp_path)
    t.update("s1", UPDATE)
    t.update("s1", {"append": {"turns": [{"role": "student"}]}, "add": {"x": 2}})
    item = json.loads((tmp_path / "s1.json").read_text())
    assert item["status"] == "active"
    assert [turn["role"] for turn in item["turns"]] == ["patient", "student"]
    assert item["helpful_count"] == 1
    assert item["x"] == 2


def test_file_store_never_creates_an_item(tmp_path):
    FileSessionTable(tmp_path).update("missing", UPDATE)
    assert list(tmp_path.iterdir()) == []


def test_file_store_refuses_a_path_like_id(tmp_path):
    with pytest.raises(ValueError):
        FileSessionTable(tmp_path).update("../escape", UPDATE)


def test_null_store_discards():
    NullSessionTable().update("s", UPDATE)


def test_selection_local_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "bridge_local", True)
    monkeypatch.setenv("SESSIONS_TABLE", "BridgeSessions")
    monkeypatch.setenv("SESSIONS_LOCAL_DIR", str(tmp_path))
    table = get_session_table()
    assert isinstance(table, FileSessionTable)
    assert table._root == tmp_path


def test_selection_local_default_dir(monkeypatch):
    monkeypatch.setattr(settings, "bridge_local", True)
    monkeypatch.delenv("SESSIONS_LOCAL_DIR", raising=False)
    assert get_session_table()._root == store.DEFAULT_LOCAL_DIR
    assert store.DEFAULT_LOCAL_DIR.parts[-2:] == (".local-data", "sessions")


def test_selection_dynamo(monkeypatch):
    monkeypatch.setattr(settings, "bridge_local", False)
    monkeypatch.setenv("SESSIONS_TABLE", "BridgeSessions")
    resource = boto3.resource(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    )
    monkeypatch.setattr(boto3, "resource", lambda name: resource)
    table = get_session_table()
    assert isinstance(table, DynamoSessionTable)
    assert table._table.name == "BridgeSessions"


def test_selection_null(monkeypatch):
    monkeypatch.setattr(settings, "bridge_local", False)
    monkeypatch.delenv("SESSIONS_TABLE", raising=False)
    assert isinstance(get_session_table(), NullSessionTable)
