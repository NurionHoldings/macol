"""Verified dial event adapter; delivery to the caller's screen belongs to the device/partner."""

import hashlib
import hmac
import os
import re
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()
SEEN: dict[str, float] = {}
WINDOW_SECONDS = 120


class DialEvent(BaseModel):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    direction: str = Field(pattern=r"^outgoing$")
    called_number: str = Field(pattern=r"^01[016789][0-9]{7,8}$")
    occurred_at: int


def _template_url() -> str:
    target = os.getenv("MACOL_PUBLIC_TEMPLATE_URL", "")
    parsed = urlsplit(target)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(503, "public HTTPS template not configured")
    return target


@router.post("/integrations/dial-events")
async def dial_event(request: Request) -> dict:
    """Accept a signed event from a contracted network adapter or opted-in caller app."""
    secret = os.getenv("MACOL_DIAL_EVENT_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "dial event integration not configured")
    body = await request.body()
    if len(body) > 2048:
        raise HTTPException(413, "event too large")
    signature = request.headers.get("x-macol-signature", "")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "invalid signature")
    event = DialEvent.model_validate_json(body)
    now = time.time()
    if abs(now - event.occurred_at) > WINDOW_SECONDS:
        raise HTTPException(409, "stale event")
    for event_id, seen_at in list(SEEN.items()):
        if now - seen_at > WINDOW_SECONDS:
            del SEEN[event_id]
    if event.event_id in SEEN:
        raise HTTPException(409, "duplicate event")
    configured = re.sub(r"\D", "", os.getenv("MACOL_RECEIVER_NUMBER", ""))
    if not configured or not hmac.compare_digest(event.called_number, configured):
        raise HTTPException(404, "template not found")
    target = _template_url()
    SEEN[event.event_id] = now
    return {"status": "matched", "template_url": target, "launch": "device_or_partner_required"}
