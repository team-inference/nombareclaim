import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db

init_db()  # ensure tables exist (equivalent to FastAPI's startup event)

client = TestClient(app)

SIGNATURE_KEY = "test_webhook_secret_123"
TIMESTAMP = "2026-06-30T10:00:00Z"

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


def _sign_payload(payload: dict, timestamp: str = TIMESTAMP, secret: str = SIGNATURE_KEY) -> str:
    """Builds a signature per Nomba's confirmed real algorithm — a
    colon-joined string of specific fields plus the nomba-timestamp
    header, base64-encoded. See services/signature.py's module
    docstring for the full citation."""
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


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unsigned_webhook_returns_401_and_does_not_crash():
    resp = client.post("/webhooks/nomba", json=PAYLOAD)
    assert resp.status_code == 401


def test_malformed_json_returns_400_not_500():
    # JSON parsing now happens BEFORE signature verification (the
    # corrected algorithm needs parsed fields to even compute the
    # expected signature), so a malformed body can never have a valid
    # signature to send in the first place — it's rejected by the
    # JSON-parse step regardless of any signature header supplied.
    bad_body = b"{not valid json"
    resp = client.post(
        "/webhooks/nomba",
        content=bad_body,
        headers={"nomba-signature": "irrelevant", "nomba-timestamp": TIMESTAMP},
    )
    assert resp.status_code == 400


def test_valid_signed_webhook_is_accepted_and_stored():
    resp = client.post("/webhooks/nomba", json=PAYLOAD, headers=_signed_headers(PAYLOAD))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "received"
    assert "id" in body

    # Confirm the nested/legacy shape also converts kobo -> naira
    # correctly through the fallback path in extract_event().
    detail = client.get(f"/api/failures/{body['id']}")
    assert detail.json()["amount"] == 15000


def test_duplicate_delivery_is_not_reprocessed():
    headers = _signed_headers(PAYLOAD)

    first = client.post("/webhooks/nomba", json=PAYLOAD, headers=headers)
    assert first.status_code == 200

    # Re-send the exact same event (same idempotency key) — simulates
    # Nomba's retry/backoff redelivering an already-processed event.
    # This is also the ONLY replay defense in this system, matching
    # Nomba's own documented recommendation (idempotency on
    # event.requestId) rather than a timestamp freshness window.
    second = client.post("/webhooks/nomba", json=PAYLOAD, headers=headers)
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
    # Nomba's real webhook payloads use lowercase event_type values
    # (payment_success, payment_failed — confirmed directly from
    # developer.nomba.com's own reference examples), while their
    # separate event-log/replay management API uses uppercase filter
    # values (PAYMENT_FAILED). This system must accept either case,
    # since NOMBA_FAILURE_EVENT_TYPES matching is case-insensitive.
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
    resp = client.post("/webhooks/nomba", json=uppercase_payload, headers=_signed_headers(uppercase_payload))
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"


def test_confirmed_flat_payload_shape_converts_kobo_to_naira():
    # The FLAT shape (data.merchantTxRef, data.amount, data.currency
    # directly under data — no transaction/order nesting at all) is
    # kept as a fallback path for a payload shape not matching the
    # officially-confirmed nested structure. Amount is in kobo here:
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
    resp = client.post("/webhooks/nomba", json=flat_payload, headers=_signed_headers(flat_payload))
    assert resp.status_code == 200
    event_id = resp.json()["id"]

    detail = client.get(f"/api/failures/{event_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["amount"] == 2500
    assert body["nomba_transaction_id"] == "ord_flat_test_001"
