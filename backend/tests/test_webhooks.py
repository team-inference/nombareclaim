import json
import os
import time

# Set required env vars before importing the app so config picks them up.
os.environ.setdefault("NOMBA_WEBHOOK_SIGNATURE_KEY", "test_signature_key_123")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_nombareclaim.db")
os.environ.setdefault("NOMBA_ACCOUNT_ID", "test-account")
os.environ.setdefault("NOMBA_SUBACCOUNT_ID", "test-subaccount")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.signature import compute_signature
from app.database import init_db

init_db()  # ensure tables exist (equivalent to FastAPI's startup event)

client = TestClient(app)

SIGNATURE_KEY = "test_signature_key_123"

PAYLOAD = {
    "event_type": "payment_failed",
    "requestId": "req-webhook-test-1",
    "data": {
        "merchant": {"userId": "user-1", "walletId": "wallet-1"},
        "transaction": {
            "transactionId": "txn-webhook-test-1",
            "type": "vact_transfer",
            "time": "2026-06-30T10:00:00Z",
            "responseCode": "51",
            "amount": "15000.00",
            "currency": "NGN",
        },
    },
}


def _signed_headers(payload: dict) -> dict:
    timestamp = str(int(time.time()))
    sig = compute_signature(payload, timestamp, SIGNATURE_KEY)
    return {"signature": sig, "timestamp": timestamp, "Content-Type": "application/json"}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unsigned_webhook_returns_401_and_does_not_crash():
    resp = client.post("/webhooks/nomba", json=PAYLOAD)
    assert resp.status_code == 401


def test_malformed_json_returns_400_not_500():
    resp = client.post(
        "/webhooks/nomba",
        data=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_valid_signed_webhook_is_accepted_and_stored():
    headers = _signed_headers(PAYLOAD)
    resp = client.post("/webhooks/nomba", content=json.dumps(PAYLOAD), headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "received"
    assert "id" in body


def test_duplicate_delivery_is_not_reprocessed():
    headers = _signed_headers(PAYLOAD)
    body_str = json.dumps(PAYLOAD)

    first = client.post("/webhooks/nomba", content=body_str, headers=headers)
    assert first.status_code == 200

    # Re-send the exact same event (same idempotency key) — simulates
    # Nomba's retry/backoff redelivering an already-processed event.
    second = client.post("/webhooks/nomba", content=body_str, headers=headers)
    assert second.status_code == 200
    assert second.json().get("status") == "duplicate_ignored"

    # Confirm only one row exists for this transaction via the API.
    listing = client.get("/api/failures", params={"page_size": 100})
    matches = [
        f for f in listing.json()["results"]
        if f["nomba_transaction_id"] == "txn-webhook-test-1"
    ]
    assert len(matches) == 1
