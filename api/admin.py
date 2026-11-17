"""
The results API: a password-gated, read-only view of every session record.

``POST /admin/login {password}`` trades the one shared password for a 12-hour
bearer token; ``GET /admin/sessions`` (the summaries, newest first) and
``GET /admin/sessions/{session_id}`` (one whole item) require it.

**The password.** ``BRIDGE_ADMIN_PASSWORD``, resolved from SSM at cold start by
the kit's ``SECRETS_FROM_SSM`` mechanism. ``voice_kit.config`` is imported here
explicitly so that resolution has run before the first request, whatever the
import order. Rotation is an SSM update plus a Lambda cold start. Under
``BRIDGE_LOCAL`` with no password set, ``local`` is accepted (``BRIDGE_LOCAL``
refuses ``ENV=production``, so this can never reach a deployed stack); unset
anywhere else, login answers 503.

**The token** is ``b64url(json {"exp": unix}) + "." + b64url(HMAC-SHA256)``,
keyed by ``sha256(b"bridge-admin-v1:" + password)``. There is no second secret,
and rotating the password invalidates every outstanding token.

Read-only by design: a shared password plus a delete button risks destroying
study data, so removals stay manual (see docs/backend/deployment.md).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

# Imported for its side effect too: ``_export_ssm_secrets`` runs on first import
# and puts BRIDGE_ADMIN_PASSWORD into os.environ on a deployed Lambda.
from voice_kit.config import settings

from api import sessions
from api.session_store import from_dynamo

logger = logging.getLogger(__name__)

PASSWORD_ENV = "BRIDGE_ADMIN_PASSWORD"
LOCAL_PASSWORD = "local"
TOKEN_LIFETIME_S = 12 * 60 * 60
WRONG_PASSWORD_DELAY_S = 1.0
_KEY_CONTEXT = b"bridge-admin-v1:"

# Abandonment is derived on read (docs/backend/README.md, Session records).
ABANDON_GRACE = timedelta(seconds=300)
NEVER_CONNECTED_AFTER = timedelta(hours=1)
DEFAULT_TIME_LIMIT_S = 300

# The GSI projection: its key attributes plus SESSION_SUMMARY_ATTRIBUTES in
# amplify/constants.ts (a test keeps the two in step). The file and memory
# stores return whole items, so the list endpoint projects to this set and every
# backend answers the same shape.
SUMMARY_FIELDS = (
    "session_id",
    "kind",
    "created_at",
    "netid",
    "scenario_id",
    "scenario_title",
    "mode",
    "status",
    "end_reason",
    "start_escalation",
    "escalation",
    "max_escalation",
    "time_limit_s",
    "started_at",
    "duration_s",
    "helpful_count",
    "escalating_count",
    "updated_at",
)

router = APIRouter(prefix="/admin")


def admin_password() -> Optional[str]:
    """The configured password, ``local`` under BRIDGE_LOCAL, else None."""
    password = os.environ.get(PASSWORD_ENV)
    if password:
        return password
    return LOCAL_PASSWORD if settings.bridge_local else None


if settings.bridge_local and not os.environ.get(PASSWORD_ENV):
    logger.warning(
        "%s is unset: the results page accepts the password %r (BRIDGE_LOCAL only)",
        PASSWORD_ENV,
        LOCAL_PASSWORD,
    )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(password: str, payload: str) -> str:
    key = hashlib.sha256(_KEY_CONTEXT + password.encode("utf-8")).digest()
    return _b64(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())


def issue_token(now: float | None = None) -> tuple[str, int]:
    """A signed token and its expiry (unix seconds). Needs a configured password."""
    password = admin_password()
    if password is None:
        raise RuntimeError("admin not configured")
    exp = int(now if now is not None else time.time()) + TOKEN_LIFETIME_S
    payload = _b64(json.dumps({"exp": exp}, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_signature(password, payload)}", exp


def verify_token(token: str, now: float | None = None) -> bool:
    """True for an untampered, unexpired token signed with the current password."""
    password = admin_password()
    if password is None or token.count(".") != 1:
        return False
    payload, signature = token.split(".")
    if not hmac.compare_digest(_signature(password, payload), signature):
        return False
    try:
        exp = json.loads(_unb64(payload))["exp"]
    except (ValueError, KeyError, TypeError):
        return False
    if not isinstance(exp, int):
        return False
    return (now if now is not None else time.time()) < exp


def require_admin(authorization: str = Header(None)) -> None:
    """FastAPI dependency: a valid ``Authorization: Bearer <token>`` or 401."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not verify_token(token.strip()):
        raise HTTPException(
            status_code=401,
            detail="Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _parse(iso: object) -> Optional[datetime]:
    if not isinstance(iso, str):
        return None
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def display_status(item: dict, now: datetime) -> str:
    """
    The status a reader shows. The runtime only writes on events, and a closed
    tab fires none, so an ``active`` item past its time limit plus 5 minutes is
    shown as ``abandoned`` and a ``started`` item over an hour old as
    ``never_connected``. Everything else is the stored status.
    """
    status = item.get("status", "started")
    if status == "active":
        started = _parse(item.get("started_at")) or _parse(item.get("created_at"))
        limit = timedelta(seconds=item.get("time_limit_s") or DEFAULT_TIME_LIMIT_S)
        if started is not None and now > started + limit + ABANDON_GRACE:
            return "abandoned"
    elif status == "started":
        created = _parse(item.get("created_at"))
        if created is not None and now > created + NEVER_CONNECTED_AFTER:
            return "never_connected"
    return status


def _with_display_status(item: dict, now: datetime) -> dict:
    return {**item, "display_status": display_status(item, now)}


class LoginRequest(BaseModel):
    password: str


async def _wrong_password_penalty() -> None:
    """A brute-force damper; the long random password is the real defence."""
    await asyncio.sleep(WRONG_PASSWORD_DELAY_S)


@router.post("/login")
async def login(body: LoginRequest) -> dict:
    password = admin_password()
    if password is None:
        raise HTTPException(status_code=503, detail="admin not configured")
    if not hmac.compare_digest(body.password.encode("utf-8"), password.encode("utf-8")):
        await _wrong_password_penalty()
        raise HTTPException(status_code=401, detail="Wrong password.")
    token, expires_at = issue_token()
    return {"token": token, "expires_at": expires_at}


@router.get("/sessions", dependencies=[Depends(require_admin)])
def list_sessions() -> dict:
    now = datetime.now(timezone.utc)
    summaries = [
        {k: v for k, v in from_dynamo(item).items() if k in SUMMARY_FIELDS}
        for item in sessions.store().list_summaries()
    ]
    summaries.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return {"sessions": [_with_display_status(s, now) for s in summaries]}


@router.get("/sessions/{session_id}", dependencies=[Depends(require_admin)])
def get_session(session_id: str) -> dict:
    try:
        item = sessions.store().get(session_id)
    except ValueError:  # the file store refuses an unsafe id
        item = None
    if item is None:
        raise HTTPException(status_code=404, detail="No such session.")
    return _with_display_status(from_dynamo(item), datetime.now(timezone.utc))
