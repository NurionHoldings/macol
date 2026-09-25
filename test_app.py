from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import ROOMS, app


def test_owner_gate_and_two_party_menu_and_role_bound_fields(monkeypatch):
    ROOMS.clear()
    monkeypatch.setenv("MACOL_OWNER_KEY", "test-owner-key")
    with TestClient(app, base_url="http://testserver") as client:
        payload = {"display_name": "예시", "introduction": "소개", "menu_titles": ["소개", "자료"]}
        assert client.post("/rooms", json=payload).status_code == 403
        created = client.post("/rooms", json=payload, headers={"X-Owner-Key": "test-owner-key"})
        assert created.status_code == 200
        room_id = created.json()["room_id"]
        room = ROOMS[room_id]
        headers = {"origin": "http://testserver"}
        with client.websocket_connect(f"/ws/{room_id}", headers=headers) as owner:
            owner.send_json({"type": "auth", "role": "owner", "token": room.owner_token})
            assert owner.receive_json()["type"] == "snapshot"
            with client.websocket_connect(f"/ws/{room_id}", headers=headers) as visitor:
                visitor.send_json({"type": "auth", "role": "visitor", "token": room.visitor_token})
                assert visitor.receive_json()["type"] == "snapshot"
                assert owner.receive_json()["type"] == "peer_joined"
                assert visitor.receive_json()["type"] == "peer_joined"
                visitor.send_json({"type": "menu", "index": 1})
                assert visitor.receive_json()["index"] == 1
                assert owner.receive_json()["index"] == 1
                visitor.send_json({"type": "field", "key": "owner_reply", "value": "위조", "revision": 1})
                assert room.fields["owner_reply"] == ""
                visitor.send_json({"type": "field", "key": "visitor_request",
                                   "value": "소개서를 원합니다", "revision": 1})
                assert visitor.receive_json()["type"] == "field"
                assert owner.receive_json()["value"] == "소개서를 원합니다"
                owner.send_json({"type": "field", "key": "owner_reply", "value": "오래된 값",
                                 "revision": 1})
                assert owner.receive_json()["type"] == "conflict"


def test_invalid_token_and_page(monkeypatch):
    ROOMS.clear()
    monkeypatch.setenv("MACOL_OWNER_KEY", "test-owner-key")
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "마컬" in page.text
        assert "no-store" in page.headers["cache-control"]
        room = client.post("/rooms", headers={"X-Owner-Key": "test-owner-key"}, json={
            "display_name": "예시", "introduction": "소개", "menu_titles": ["자료"],
        }).json()
        with client.websocket_connect(f"/ws/{room['room_id']}", headers={
            "origin": "http://testserver",
        }) as ws:
            ws.send_json({"type": "auth", "role": "owner", "token": "incorrect"})
            try:
                ws.receive_json()
                assert False, "invalid token accepted"
            except WebSocketDisconnect as error:
                assert error.code == 1008
