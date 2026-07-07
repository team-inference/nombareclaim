import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db
from app.config import settings
from app.services.signature import extract_event
from app.services import nomba_client

init_db()

client = TestClient(app)

SIGNATURE_KEY = "test_webhook_secret_123"


def _signed_headers(raw_body: bytes) -> dict:
    sig = hmac.new(SIGNATURE_KEY.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {"nomba-signature": sig, "Content-Type": "application/json"}


# A real payment_success example shape, per
# developer.nomba.com/docs/products/accept-payment/sandbox-testing —
# nested under transaction/order, with a confirmed customerEmail field
# and NAIRA-denominated (not kobo) amount fields.
OFFICIAL_SHAPE_PAYLOAD = {
    "event_type": "payment_success",
    "requestId": "req-official-shape-1",
    "data": {
        "merchant": {"userId": "merchant-user-1"},
        "transaction": {
            "fee": 0.28,
            "type": "online_checkout",
            "transactionId": "WEB-ONLINE_C-abc123-official-shape",
            "merchantTxRef": "txref-official-shape-1",
            "transactionAmount": 4000.00,
            "time": "2026-07-06T10:00:00Z",
        },
        "order": {
            "amount": 4000.00,
            "orderId": "order-official-shape-1",
            "accountId": "sub-account-1",
            "customerEmail": "official-shape-customer@example.com",
            "orderReference": "reclaim-official-shape-1",
            "paymentMethod": "card_payment",
            "currency": "NGN",
        },
    },
}


def test_extract_event_reads_official_nested_shape_correctly():
    parsed = extract_event(OFFICIAL_SHAPE_PAYLOAD)

    assert parsed.event_type == "payment_success"
    assert parsed.merchant_tx_ref == "txref-official-shape-1"
    assert parsed.transaction_id == "WEB-ONLINE_C-abc123-official-shape"
    assert parsed.order_reference == "reclaim-official-shape-1"
    assert parsed.customer_email == "official-shape-customer@example.com"
    assert parsed.currency == "NGN"
    # Confirmed-naira field populated; kobo field absent for this shape.
    assert parsed.amount_naira == 4000.00
    assert parsed.amount_kobo is None


def test_webhook_ingestion_stores_naira_amount_directly_not_divided_by_100():
    # Uses PAYMENT_FAILED so it's actually stored as a FailureEvent —
    # same nested shape, different event_type, to exercise the full
    # ingestion path rather than just extract_event() in isolation.
    payload = {
        "event_type": "PAYMENT_FAILED",
        "requestId": "req-official-shape-2",
        "data": {
            "transaction": {
                "merchantTxRef": "txref-official-shape-2",
                "transactionId": "WEB-ONLINE_C-official-shape-2",
                "transactionAmount": 7500.00,
                "responseCode": "51",
            },
            "order": {
                "orderReference": "reclaim-official-shape-2",
                "customerEmail": "official-shape-2@example.com",
                "currency": "NGN",
            },
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    resp = client.post("/webhooks/nomba", content=raw_body, headers=_signed_headers(raw_body))
    assert resp.status_code == 200
    event_id = resp.json()["id"]

    detail = client.get(f"/api/failures/{event_id}").json()
    # 7500.00 stored directly as naira — NOT divided by 100 (which
    # would have wrongly stored 75).
    assert detail["amount"] == 7500
    assert detail["has_contact"] is True


def test_checkout_path_prefix_and_root_host_are_environment_aware(monkeypatch):
    monkeypatch.setattr(settings, "NOMBA_API_BASE_URL", "https://sandbox.nomba.com/v1")
    assert nomba_client._is_sandbox() is True
    assert nomba_client._checkout_path_prefix() == "/sandbox/checkout"
    assert nomba_client._root_host() == "https://sandbox.nomba.com"

    monkeypatch.setattr(settings, "NOMBA_API_BASE_URL", "https://api.nomba.com/v1")
    assert nomba_client._is_sandbox() is False
    assert nomba_client._checkout_path_prefix() == "/v1/checkout"
    assert nomba_client._root_host() == "https://api.nomba.com"
