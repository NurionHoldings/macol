import hashlib
import hmac
import json
import time
import uuid

from fastapi.testclient import TestClient

from app import app
from call_bridge import SEEN


def test_signed_dial_event_requires_matching_number_and_rejects_replay(monkeypatch):
    monkeypatch.setenv("MACOL_DIAL_EVENT_SECRET", "s" * 48)
    monkeypatch.setenv("MACOL_RECEIVER_NUMBER", "01000000000")
    monkeypatch.setenv("MACOL_PUBLIC_TEMPLATE_URL", "https://example.org/profile")
    SEEN.clear()
    client = TestClient(app)
    event = {"event_id": uuid.uuid4().hex, "direction": "outgoing",
             "called_number": "01000000000", "occurred_at": int(time.time())}
    raw = json.dumps(event).encode()
    signature = hmac.new(b"s" * 48, raw, hashlib.sha256).hexdigest()
    result = client.post("/integrations/dial-events", content=raw,
                         headers={"x-macol-signature": signature})
    assert result.status_code == 200
    assert result.json() == {"status": "matched", "template_url": "https://example.org/profile",
                             "launch": "device_or_partner_required"}
    assert client.post("/integrations/dial-events", content=raw,
                       headers={"x-macol-signature": signature}).status_code == 409
    assert client.post("/integrations/dial-events", content=raw,
                       headers={"x-macol-signature": "bad"}).status_code == 401


def test_bridge_rejects_unmatched_and_stale_events(monkeypatch):
    monkeypatch.setenv("MACOL_DIAL_EVENT_SECRET", "s" * 48)
    monkeypatch.setenv("MACOL_RECEIVER_NUMBER", "01000000000")
    monkeypatch.setenv("MACOL_PUBLIC_TEMPLATE_URL", "https://example.org/profile")
    client = TestClient(app)
    for number, timestamp, expected in [
        ("01000000001", int(time.time()), 404),
        ("01000000000", int(time.time()) - 600, 409),
    ]:
        raw = json.dumps({"event_id": uuid.uuid4().hex, "direction": "outgoing",
                          "called_number": number, "occurred_at": timestamp}).encode()
        signature = hmac.new(b"s" * 48, raw, hashlib.sha256).hexdigest()
        assert client.post("/integrations/dial-events", content=raw,
                           headers={"x-macol-signature": signature}).status_code == expected
