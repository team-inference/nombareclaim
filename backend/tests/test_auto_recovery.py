import base64
import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db
from app.config import settings
from app.services import nomba_client

init_db()

client = TestClient(app)

SIGNATURE_KEY = "test_webhook_secret_123"
TIMESTAMP = "2026-06-30T10:00:00Z"


def _sign_payload(payload: dict, timestamp: str = TIMESTAMP, secret: str = SIGNATURE_KEY) -> str:
    data = payload.get("data", {})
    merchant = data.get("merchant", {})
    transaction = data.get("transaction", {})
    response_code = transaction.get("responseCode") or ""
    if str(response_code).lower() == "null":
        response_code = ""

    hashing_payload = (
        f"{payload.get('event_type', '')}:{payload.get('requestId', '')}:"
        f"{merchant.get('userId', '')}:{merchant.get('walletId', '')}:"
        f"{transaction.get('transactionId', '')}:{transaction.get('type', '')}:"
        f"{transaction.get('time', '')}:{response_code}:{timestamp}"
    )
    digest = hmac.new(secret.encode("utf-8"), hashing_payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _signed_headers(payload: dict, timestamp: str = TIMESTAMP) -> dict:
    return {
        "nomba-signature": _sign_payload(payload, timestamp),
        "nomba-timestamp": timestamp,
        "Content-Type": "application/json",
    }


async def _fake_create_checkout_order(**kwargs):
    return {
        "checkout_url": "https://sandbox.nomba.example/checkout/fake-order",
        "order_reference": kwargs["customer_reference"],
    }


def test_auto_recovery_fires_when_enabled_with_contact_and_score(monkeypatch):
    # Automation is off by default — explicitly turn it on for this
    # test only, and stub the outbound Nomba call so no real network
    # request is attempted.
    monkeypatch.setattr(settings, "RECOVERY_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_RECOVERY_MIN_SCORE", 0)
    monkeypatch.setattr(nomba_client, "create_checkout_order", _fake_create_checkout_order)

    payload = {
        "event_type": "PAYMENT_FAILED",
        "requestId": "req-auto-recovery-1",
        "data": {
            "merchantTxRef": "txn-auto-recovery-1",
            "amount": 500000,  # kobo -> ₦5,000, response code 51 = insufficient funds
            "currency": "NGN",
            "customerEmail": "customer@example.com",
            "transaction": {"responseCode": "51"},
        },
    }
    resp = client.post("/webhooks/nomba", json=payload, headers=_signed_headers(payload))
    assert resp.status_code == 200
    event_id = resp.json()["id"]

    # Background task (classification + auto-recovery) runs within the
    # TestClient request/response cycle, but poll briefly to be safe
    # rather than assuming exact synchronous timing.
    detail = None
    for _ in range(20):
        detail = client.get(f"/api/failures/{event_id}").json()
        if detail["status"] == "RECOVERY_TRIGGERED":
            break
        time.sleep(0.05)

    assert detail is not None
    assert detail["has_contact"] is True
    assert detail["status"] == "RECOVERY_TRIGGERED"
    assert detail["recovery_checkout_url"] == "https://sandbox.nomba.example/checkout/fake-order"


def test_auto_recovery_does_not_fire_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "RECOVERY_AUTOMATION_ENABLED", False)
    monkeypatch.setattr(nomba_client, "create_checkout_order", _fake_create_checkout_order)

    payload = {
        "event_type": "PAYMENT_FAILED",
        "requestId": "req-auto-recovery-2",
        "data": {
            "merchantTxRef": "txn-auto-recovery-2",
            "amount": 500000,
            "currency": "NGN",
            "customerEmail": "customer2@example.com",
            "transaction": {"responseCode": "51"},
        },
    }
    resp = client.post("/webhooks/nomba", json=payload, headers=_signed_headers(payload))
    assert resp.status_code == 200
    event_id = resp.json()["id"]

    time.sleep(0.2)
    detail = client.get(f"/api/failures/{event_id}").json()
    # Should have been classified, but never auto-triggered into
    # RECOVERY_TRIGGERED, since automation is off.
    assert detail["status"] == "CLASSIFIED"
    assert detail["has_contact"] is True


def test_configurable_failure_event_type(monkeypatch):
    # Simulates discovering that Nomba's real failure event name is
    # something other than PAYMENT_FAILED — should be a pure env/config
    # change, no code change, per config.py's own comment. (In practice
    # we've since confirmed the real name IS payment_failed, per
    # developer.nomba.com — this test just proves the mechanism works
    # regardless of what the real name turns out to be.)
    monkeypatch.setattr(settings, "NOMBA_FAILURE_EVENT_TYPES", {"PAYMENT_DECLINED"})

    payload = {
        "event_type": "PAYMENT_DECLINED",
        "requestId": "req-custom-event-type",
        "data": {
            "merchantTxRef": "txn-custom-event-type",
            "amount": 100000,
            "currency": "NGN",
        },
    }
    resp = client.post("/webhooks/nomba", json=payload, headers=_signed_headers(payload))
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"

    # And the OLD default name should now be ignored, confirming the
    # set is actually driving behavior rather than a hardcoded check.
    payload2 = {**payload, "event_type": "PAYMENT_FAILED", "requestId": "req-custom-event-type-2",
                "data": {**payload["data"], "merchantTxRef": "txn-custom-event-type-2"}}
    resp2 = client.post("/webhooks/nomba", json=payload2, headers=_signed_headers(payload2))
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"
