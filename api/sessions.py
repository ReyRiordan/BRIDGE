"""
POST /sessions — create the session record and issue its id.

The SPA calls this once per case, after the student has signed in with their
NetID, and uses the returned ``session_id`` for every voice call. The id keeps
the ``<scenario_id>--<uuid>`` shape the runtime parses its scenario from, but it
is now server-issued. The NetID is stored here and never leaves the control
plane: the runtime only updates the item by id, so personal data stays out of
the voice container, its logs and the signaling path.

Unauthenticated, like the /voice endpoints (see ``authorize`` in main.py):
acceptable for the closed pilot, where a mislabelled record is the risk rather
than a breach.
"""

import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api import scenario
from api.session_store import SessionStore, get_session_store

# Kept in step with web/src/identity.ts.
_NETID = re.compile(r"^[a-z]+[0-9]*$")
USER_AGENT_MAX = 256

router = APIRouter()


def normalize_netid(raw: str) -> Optional[str]:
    """Trim, lowercase, drop an ``@…`` suffix; None unless it looks like a NetID."""
    netid = raw.strip().lower().split("@", 1)[0].strip()
    if 2 <= len(netid) <= 20 and _NETID.match(netid):
        return netid
    return None


class CreateSessionRequest(BaseModel):
    netid: str
    scenario_id: str
    mode: Literal["voice", "text"]


@lru_cache(maxsize=1)
def store() -> SessionStore:
    """One store per process; tests swap it with ``store.cache_clear`` + patch."""
    return get_session_store()


@router.post("/sessions")
def create_session(body: CreateSessionRequest, request: Request) -> dict:
    netid = normalize_netid(body.netid)
    if netid is None:
        raise HTTPException(status_code=422, detail="Enter a valid NetID.")
    s = scenario._scenarios.get(body.scenario_id)
    if s is None:
        raise HTTPException(
            status_code=422, detail=f"Unknown scenario {body.scenario_id!r}"
        )
    session_id = f"{body.scenario_id}--{uuid.uuid4()}"
    now = datetime.now(timezone.utc).isoformat()
    store().put(
        {
            "session_id": session_id,
            "kind": "session",
            "created_at": now,
            "updated_at": now,
            "netid": netid,
            "scenario_id": body.scenario_id,
            "scenario_title": s["title"],
            "mode": body.mode,
            "user_agent": request.headers.get("user-agent", "")[:USER_AGENT_MAX],
            "status": "started",
            "end_reason": None,
            "reason": None,
            "start_escalation": s["point_bar"]["start"],
            "escalation": s["point_bar"]["start"],
            "time_limit_s": s.get("time_limit", 300),
            "helpful_count": 0,
            "escalating_count": 0,
            "turns": [],
            "timeline": [],
        }
    )
    return {"session_id": session_id}
