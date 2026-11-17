"""
Session-record storage for the control plane: the API creates each item and
(later) reads it back for the results page. The runtime only updates it — see
``runtime/bridge/store.py``, whose selection rules this mirrors:

- ``BRIDGE_LOCAL`` -> one JSON file per session under ``SESSIONS_LOCAL_DIR``
  (default ``<repo>/.local-data/sessions``, the same dir the runtime writes to);
- ``SESSIONS_TABLE`` set -> DynamoDB;
- neither -> an in-memory dict (unit tests).

Floats go to and from ``Decimal`` at the Dynamo boundary; callers only ever see
plain JSON types.
"""

import json
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Protocol

from voice_kit.config import settings

# Exported from amplify/constants.ts (SESSIONS_INDEX); the GSI that lists items
# by `kind` ("session"), newest `created_at` first.
SESSIONS_INDEX = "by_created"

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_DIR = _REPO_ROOT / ".local-data" / "sessions"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


class SessionStore(Protocol):
    def put(self, item: dict) -> None: ...
    def get(self, session_id: str) -> Optional[dict]: ...
    def list_summaries(self) -> list[dict]: ...


def to_dynamo(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_dynamo(v) for v in value]
    return value


def from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


def _newest_first(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)


class DynamoSessionStore:
    def __init__(self, table_name: str, resource=None):
        import boto3

        self._table = (resource or boto3.resource("dynamodb")).Table(table_name)

    def put(self, item: dict) -> None:
        self._table.put_item(Item=to_dynamo(item))

    def get(self, session_id: str) -> Optional[dict]:
        item = self._table.get_item(Key={"session_id": session_id}).get("Item")
        return from_dynamo(item) if item is not None else None

    def list_summaries(self) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        kwargs = {
            "IndexName": SESSIONS_INDEX,
            "KeyConditionExpression": Key("kind").eq("session"),
            "ScanIndexForward": False,
        }
        items: list[dict] = []
        while True:
            page = self._table.query(**kwargs)
            items.extend(from_dynamo(i) for i in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


class FileSessionStore:
    def __init__(self, root: Path):
        self._root = Path(root)

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise ValueError(f"unsafe session id {session_id!r}")
        return self._root / f"{session_id}.json"

    def put(self, item: dict) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._path(item["session_id"]).write_text(
            json.dumps(item, indent=2), encoding="utf-8"
        )

    def get(self, session_id: str) -> Optional[dict]:
        path = self._path(session_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_summaries(self) -> list[dict]:
        if not self._root.is_dir():
            return []
        return _newest_first(
            [
                json.loads(p.read_text(encoding="utf-8"))
                for p in self._root.glob("*.json")
            ]
        )


class MemorySessionStore:
    def __init__(self):
        self.items: dict[str, dict] = {}

    def put(self, item: dict) -> None:
        self.items[item["session_id"]] = item

    def get(self, session_id: str) -> Optional[dict]:
        return self.items.get(session_id)

    def list_summaries(self) -> list[dict]:
        return _newest_first(list(self.items.values()))


def get_session_store() -> SessionStore:
    if settings.bridge_local:
        return FileSessionStore(
            Path(os.environ.get("SESSIONS_LOCAL_DIR") or DEFAULT_LOCAL_DIR)
        )
    table_name = os.environ.get("SESSIONS_TABLE")
    if table_name:
        return DynamoSessionStore(table_name)
    return MemorySessionStore()
