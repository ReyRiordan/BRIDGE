"""
Session-record storage backends for the runtime: pipecat-free and synchronous.

The control plane creates each session item (``POST /sessions``); the runtime
only ever **updates** it, by session id, from the recorder's background writer
(``recorder.py``), which calls :meth:`SessionTable.update` inside
``asyncio.to_thread``. Nothing here runs on the event loop.

An update is one dict with three optional parts::

    {
        "set":    {"status": "active", "escalation": 3},     # overwrite
        "append": {"turns": [...], "timeline": [...]},       # list append
        "add":    {"helpful_count": 1},                      # numeric add
    }

Every backend refuses to CREATE an item: Dynamo through ``attribute_exists``,
the file store by skipping a missing file. An id the API never issued
therefore never leaves junk behind.

Selection (:func:`get_session_table`) mirrors ``api/session_store.py``:
``BRIDGE_LOCAL`` -> a JSON file per session under ``SESSIONS_LOCAL_DIR``;
``SESSIONS_TABLE`` set -> DynamoDB; neither -> a no-op that logs once.
"""

import json
import logging
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# runtime/bridge/store.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOCAL_DIR = _REPO_ROOT / ".local-data" / "sessions"

# Session ids become file names locally: refuse anything that could leave the dir.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


class SessionTable(Protocol):
    def update(self, session_id: str, update: dict) -> None: ...


def is_empty(update: dict) -> bool:
    """True when the update would change nothing (an empty append included)."""
    appends = update.get("append") or {}
    return not (update.get("set") or update.get("add") or any(appends.values()))


def to_dynamo(value: Any) -> Any:
    """Floats -> Decimal, recursively (boto3's resource layer rejects floats)."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_dynamo(v) for v in value]
    return value


def build_update_expression(update: dict) -> dict:
    """The ``UpdateItem`` keyword arguments for one update (minus the key)."""
    names: dict = {}
    values: dict = {}
    sets: list = []
    adds: list = []
    for i, (attr, value) in enumerate(update.get("set", {}).items()):
        names[f"#s{i}"] = attr
        values[f":s{i}"] = to_dynamo(value)
        sets.append(f"#s{i} = :s{i}")
    appends = {k: v for k, v in update.get("append", {}).items() if v}
    if appends:
        values[":empty"] = []
    for i, (attr, items) in enumerate(appends.items()):
        names[f"#a{i}"] = attr
        values[f":a{i}"] = to_dynamo(list(items))
        sets.append(f"#a{i} = list_append(if_not_exists(#a{i}, :empty), :a{i})")
    for i, (attr, n) in enumerate(update.get("add", {}).items()):
        names[f"#c{i}"] = attr
        values[f":c{i}"] = to_dynamo(n)
        adds.append(f"#c{i} :c{i}")
    clauses = []
    if sets:
        clauses.append("SET " + ", ".join(sets))
    if adds:
        clauses.append("ADD " + ", ".join(adds))
    return {
        "UpdateExpression": " ".join(clauses),
        "ConditionExpression": "attribute_exists(session_id)",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }


class DynamoSessionTable:
    """One conditional ``UpdateItem`` per update. The runtime role may do no more."""

    def __init__(self, table_name: str, resource=None):
        import boto3

        self._table = (resource or boto3.resource("dynamodb")).Table(table_name)

    def update(self, session_id: str, update: dict) -> None:
        if is_empty(update):
            return
        self._table.update_item(
            Key={"session_id": session_id}, **build_update_expression(update)
        )


class FileSessionTable:
    """Read-modify-write of ``<root>/<session_id>.json`` (local dev only)."""

    def __init__(self, root: Path):
        self._root = Path(root)

    def update(self, session_id: str, update: dict) -> None:
        if not _SAFE_ID.match(session_id):
            raise ValueError(f"unsafe session id {session_id!r}")
        path = self._root / f"{session_id}.json"
        if not path.is_file():
            # Mirrors the Dynamo condition: never create an item.
            logger.warning("[%s] no session record to update", session_id)
            return
        item = json.loads(path.read_text(encoding="utf-8"))
        item.update(update.get("set", {}))
        for attr, items in update.get("append", {}).items():
            item[attr] = list(item.get(attr) or []) + list(items)
        for attr, n in update.get("add", {}).items():
            item[attr] = (item.get(attr) or 0) + n
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(item, indent=2), encoding="utf-8")
        tmp.replace(path)


class NullSessionTable:
    """Discards every update (unit tests, or a deploy without a table)."""

    _warned = False

    def update(self, session_id: str, update: dict) -> None:
        if not NullSessionTable._warned:
            NullSessionTable._warned = True
            logger.info("No session store configured; session records are discarded")


def local_dir() -> Path:
    return Path(os.environ.get("SESSIONS_LOCAL_DIR") or DEFAULT_LOCAL_DIR)


def get_session_table() -> SessionTable:
    from voice_kit.config import settings

    if settings.bridge_local:
        return FileSessionTable(local_dir())
    table_name = os.environ.get("SESSIONS_TABLE")
    if table_name:
        return DynamoSessionTable(table_name)
    return NullSessionTable()
