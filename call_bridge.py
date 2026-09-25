"""Verified dial event adapter; delivery to the caller's screen belongs to the device/partner."""

import hashlib
import hmac
import os
import re
import time
from html import escape
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
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


@router.get("/public/templates/{called_number}")
def public_template(called_number: str) -> dict:
    """Public directory for an opted-in caller app; never distribute the server HMAC key."""
    configured = re.sub(r"\D", "", os.getenv("MACOL_RECEIVER_NUMBER", ""))
    if not configured or not hmac.compare_digest(called_number, configured):
        raise HTTPException(404, "template not found")
    return {"template_url": _template_url(), "display_name": os.getenv("MACOL_PROFILE_NAME", "마컬")[:80]}


@router.get("/profile", response_class=HTMLResponse)
def public_profile() -> HTMLResponse:
    """Minimal public profile for a cellular call; voice remains on the mobile network."""
    name = escape(os.getenv("MACOL_PROFILE_NAME", "인석")[:80])
    intro = escape(os.getenv("MACOL_PROFILE_INTRO", "용건을 선택해 주세요.")[:500])
    menus = [escape(s.strip()[:80]) for s in os.getenv(
        "MACOL_PROFILE_MENUS", "플랫폼 제휴문의,개발문의,이용문의,개인적인 통화"
    ).split(",") if s.strip()][:8]
    buttons = "".join(f"<button type='button' onclick='selectMenu(this)'>{m}</button>" for m in menus)
    page = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>마컬 · {name}</title><style>body{{font:18px sans-serif;max-width:36rem;"
            "margin:2rem auto;padding:1rem;background:#071622;color:white}}button{display:block;"
            "width:100%;padding:1rem;margin:.7rem 0;background:#e4f5fa;color:#071622;border:0;"
            "border-radius:12px;font-size:1rem}</style></head><body>"
            f"<h1>{name}</h1><p>{intro}</p><p>통화 중에도 메뉴를 볼 수 있습니다.</p>{buttons}"
            "<p id='selection' role='status'></p><p>메뉴 전달과 소유자 화면 동기화는 "
            "현재 이 화면에 연결되지 않았습니다.</p>"
            "<script>function selectMenu(b){document.getElementById('selection').textContent="
            "'선택한 메뉴: '+b.textContent}</script></body></html>")
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "Content-Security-Policy":
                        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"})


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
