"""Two-party browser voice and synchronized menu prototype for MACOL."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from call_bridge import router as call_bridge_router


class RoomRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    introduction: str = Field(min_length=1, max_length=500)
    menu_titles: list[str] = Field(min_length=1, max_length=8)


@dataclass
class Room:
    owner_token: str
    visitor_token: str
    display_name: str
    introduction: str
    menus: list[str]
    created_at: float = field(default_factory=time.time)
    sockets: dict[str, WebSocket] = field(default_factory=dict)
    menu_index: int = 0
    revision: int = 0
    call_requested: bool = False
    call_approved: bool = False
    fields: dict[str, str] = field(default_factory=lambda: {"visitor_request": "", "owner_reply": ""})
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


app = FastAPI(title="MACOL shared voice prototype")
app.include_router(call_bridge_router)
ROOMS: dict[str, Room] = {}
ROOM_TTL_SECONDS = 3600
MAX_ROOMS = 100


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ice")
def ice_config() -> dict[str, list[dict[str, str]]]:
    """Optional STUN for cross-network trials; TURN needs a separate scoped credential service."""
    url = os.getenv("MACOL_STUN_URL", "")
    if url and not url.startswith(("stun:", "stuns:")):
        raise HTTPException(status_code=503, detail="invalid ICE configuration")
    return {"iceServers": [{"urls": url}] if url else []}


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(
        Path(__file__).with_name("index.html").read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self' ws: wss:; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(Path(__file__).with_name("manifest.webmanifest"),
                        media_type="application/manifest+json")


@app.get("/icon.svg")
def icon() -> FileResponse:
    return FileResponse(Path(__file__).with_name("icon.svg"), media_type="image/svg+xml")


@app.post("/rooms")
def create_room(payload: RoomRequest, request: Request, x_owner_key: str | None = Header(None)) -> dict:
    configured = os.getenv("MACOL_OWNER_KEY")
    if not configured or not x_owner_key or not secrets.compare_digest(x_owner_key, configured):
        raise HTTPException(status_code=403, detail="owner key required")
    if any(not title.strip() or len(title) > 80 or "<" in title or ">" in title for title in payload.menu_titles):
        raise HTTPException(status_code=422, detail="invalid menu")
    now = time.time()
    for room_id, room in list(ROOMS.items()):
        if now - room.created_at > ROOM_TTL_SECONDS and not room.sockets:
            del ROOMS[room_id]
    if len(ROOMS) >= MAX_ROOMS:
        raise HTTPException(status_code=429, detail="room limit reached")
    room_id = secrets.token_urlsafe(18)
    ROOMS[room_id] = Room(
        owner_token=secrets.token_urlsafe(32), visitor_token=secrets.token_urlsafe(32),
        display_name=payload.display_name.strip(), introduction=payload.introduction.strip(),
        menus=[title.strip() for title in payload.menu_titles],
    )
    origin = str(request.base_url).rstrip("/")
    return {
        "room_id": room_id,
        "owner_url": f"{origin}/#room={room_id}&role=owner&token={ROOMS[room_id].owner_token}",
        "visitor_url": f"{origin}/#room={room_id}&role=visitor&token={ROOMS[room_id].visitor_token}",
        "expires_in_seconds": ROOM_TTL_SECONDS,
    }


async def send(socket: WebSocket, data: dict) -> None:
    await socket.send_json(data)


@app.websocket("/ws/{room_id}")
async def room_socket(socket: WebSocket, room_id: str) -> None:
    origin = socket.headers.get("origin", "")
    if urlsplit(origin).netloc != socket.headers.get("host") or urlsplit(origin).scheme not in {
        "http", "https"
    }:
        await socket.close(code=1008)
        return
    room = ROOMS.get(room_id)
    if room is None or time.time() - room.created_at > ROOM_TTL_SECONDS:
        await socket.close(code=1008)
        return
    await socket.accept()
    role = ""
    try:
        auth = await asyncio.wait_for(socket.receive_json(), timeout=10)
        if not isinstance(auth, dict) or auth.get("type") != "auth":
            await socket.close(code=1008)
            return
        proposed = auth.get("role")
        provided = auth.get("token")
        if proposed not in {"owner", "visitor"} or not isinstance(provided, str):
            await socket.close(code=1008)
            return
        expected = room.owner_token if proposed == "owner" else room.visitor_token
        if not secrets.compare_digest(provided, expected):
            await socket.close(code=1008)
            return
        role = proposed
        async with room.lock:
            if role in room.sockets:
                await socket.close(code=1008)
                return
            room.sockets[role] = socket
            await send(socket, {
                "type": "snapshot", "role": role, "display_name": room.display_name,
                "introduction": room.introduction, "menus": room.menus,
                "menu_index": room.menu_index, "revision": room.revision,
                "fields": room.fields, "peer_present": len(room.sockets) == 2,
                "call_requested": room.call_requested, "call_approved": room.call_approved,
            })
            peer = room.sockets.get("visitor" if role == "owner" else "owner")
            if peer:
                await send(peer, {"type": "peer_joined"})
                await send(socket, {"type": "peer_joined"})
        while True:
            message = await socket.receive_json()
            if not isinstance(message, dict) or len(str(message)) > 18000:
                await socket.close(code=1009)
                break
            kind = message.get("type")
            async with room.lock:
                peer = room.sockets.get("visitor" if role == "owner" else "owner")
                if kind == "menu":
                    index = message.get("index")
                    if type(index) is not int or not 0 <= index < len(room.menus):
                        continue
                    room.menu_index = index
                    room.revision += 1
                    event = {"type": "menu", "index": index, "revision": room.revision, "by": role}
                    await send(socket, event)
                    if peer:
                        await send(peer, event)
                elif kind == "field":
                    key, value, expected_version = (
                        message.get("key"), message.get("value"), message.get("revision")
                    )
                    allowed = "visitor_request" if role == "visitor" else "owner_reply"
                    if key != allowed or not isinstance(value, str) or len(value) > 2000:
                        continue
                    if type(expected_version) is not int or expected_version != room.revision:
                        await send(socket, {"type": "conflict", "revision": room.revision,
                                            "fields": room.fields})
                        continue
                    room.fields[key] = value
                    room.revision += 1
                    event = {"type": "field", "key": key, "value": value,
                             "revision": room.revision, "by": role}
                    await send(socket, event)
                    if peer:
                        await send(peer, event)
                elif kind == "request_call" and role == "visitor":
                    if room.fields["visitor_request"].strip():
                        room.call_requested = True
                        event = {"type": "call_requested"}
                        await send(socket, event)
                        if peer:
                            await send(peer, event)
                elif kind == "decide_call" and role == "owner" and room.call_requested:
                    approved = message.get("approved")
                    if type(approved) is bool:
                        room.call_approved = approved
                        room.call_requested = False
                        event = {"type": "call_decision", "approved": approved}
                        await send(socket, event)
                        if peer:
                            await send(peer, event)
                elif kind in {"offer", "answer", "ice"} and peer and room.call_approved:
                    value = message.get("value")
                    if isinstance(value, dict) and len(str(value)) < 12000:
                        await send(peer, {"type": kind, "value": value})
                elif kind == "hangup" and peer:
                    await send(peer, {"type": "hangup"})
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError):
        pass
    finally:
        if role:
            async with room.lock:
                if room.sockets.get(role) is socket:
                    del room.sockets[role]
                    peer = room.sockets.get("visitor" if role == "owner" else "owner")
                    if peer:
                        await send(peer, {"type": "peer_left"})
