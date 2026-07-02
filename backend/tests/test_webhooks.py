import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db

init_db()  # ensure tables exist (equivalent to FastAPI's startup event)

client = TestClient(app)

SIGNATURE_KEY = "test_webhook_secret_123"

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
            "amount": 1500000,  # kobo -> ₦15,000 (nested/legacy shape, kept as a fallback-path test)
            "currency": "NGN",
        },
    },
}


def _signed_headers(raw_body: bytes) -> dict:
    sig = hmac.new(SIGNATURE_KEY.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {"nomba-signature": sig, "Content-Type": "application/json"}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unsigned_webhook_returns_401_and_does_not_crash():
    resp = client.post("/webhooks/nomba", json=PAYLOAD)
    assert resp.status_code == 401


def test_malformed_json_returns_400_not_500():
    # A malformed body still needs a VALID signature to get past
    # verification first (verification now happens before JSON
    # parsing) — sign the malformed bytes themselves, then confirm the
    # JSON parse step is what correctly rejects it with 400.
    bad_body = b"{not valid json"
    resp = client.post(
        "/webhooks/nomba",
        content=bad_body,
        headers=_signed_headers(bad_body),
    )
    assert resp.status_code == 400


def test_valid_signed_webhook_is_accepted_and_stored():
    raw_body = json.dumps(PAYLOAD).encode("utf-8")
    resp = client.post("/webhooks/nomba", content=raw_body, headers=_signed_headers(raw_body))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "received"
    assert "id" in body

    # Confirm the nested/legacy shape also converts kobo -> naira
    # correctly through the fallback path in extract_event().
    detail = client.get(f"/api/failures/{body['id']}")
    assert detail.json()["amount"] == 15000


def test_duplicate_delivery_is_not_reprocessed():
    raw_body = json.dumps(PAYLOAD).encode("utf-8")
    headers = _signed_headers(raw_body)

    first = client.post("/webhooks/nomba", content=raw_body, headers=headers)
    assert first.status_code == 200

    # Re-send the exact same event (same idempotency key) — simulates
    # Nomba's retry/backoff redelivering an already-processed event.
    # This is also the ONLY replay defense in this system, matching
    # Nomba's own documented recommendation (idempotency on
    # event.requestId) rather than a timestamp freshness window.
    second = client.post("/webhooks/nomba", content=raw_body, headers=headers)
    assert second.status_code == 200
    assert second.json().get("status") == "duplicate_ignored"

    # Confirm only one row exists for this transaction via the API.
    listing = client.get("/api/failures", params={"page_size": 100})
    matches = [
        f for f in listing.json()["results"]
        if f["nomba_transaction_id"] == "txn-webhook-test-1"
    ]
    assert len(matches) == 1


def test_uppercase_event_type_is_accepted_same_as_lowercase():
    # Nomba's real API docs use uppercase event type values
    # (PAYMENT_FAILED, PAYMENT_SUCCESS) while an unrelated training
    # quiz payload used lowercase under a different field name. This
    # system must accept either — a real webhook arriving in whichever
    # convention Nomba actually uses in practice must not be silently
    # dropped as "ignored".
    uppercase_payload = {
        "event_type": "PAYMENT_FAILED",
        "requestId": "req-webhook-test-uppercase",
        "data": {
            "merchant": {"userId": "user-2", "walletId": "wallet-2"},
            "transaction": {
                "transactionId": "txn-webhook-test-uppercase",
                "type": "vact_transfer",
                "time": "2026-06-30T10:00:00Z",
                "responseCode": "05",
                "amount": 700000,  # kobo -> ₦7,000
                "currency": "NGN",
            },
        },
    }
    raw_body = json.dumps(uppercase_payload).encode("utf-8")
    resp = client.post("/webhooks/nomba", content=raw_body, headers=_signed_headers(raw_body))
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"


def test_confirmed_flat_payload_shape_converts_kobo_to_naira():
    # This is the CONFIRMED shape from Nomba's own training material
    # (data.merchantTxRef, data.amount, data.currency directly under
    # data — not nested under data.transaction.*). Amount is in kobo:
    # 250000 kobo must become ₦2,500 stored, matching the shared
    # dashboard API contract which displays naira throughout.
    flat_payload = {
        "event_type": "PAYMENT_FAILED",
        "requestId": "req-flat-shape-test",
        "data": {
            "merchantTxRef": "ord_flat_test_001",
            "amount": 250000,  # kobo -> should become 2500 naira
            "currency": "NGN",
        },
    }
    raw_body = json.dumps(flat_payload).encode("utf-8")
    resp = client.post("/webhooks/nomba", content=raw_body, headers=_signed_headers(raw_body))
    assert resp.status_code == 200
    event_id = resp.json()["id"]

    detail = client.get(f"/api/failures/{event_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["amount"] == 2500
    assert body["nomba_transaction_id"] == "ord_flat_test_001"
