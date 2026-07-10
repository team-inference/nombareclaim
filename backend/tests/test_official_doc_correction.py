import base64
import hashlib
import hmac

from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db
from app.config import settings
from app.services.signature import extract_event
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
    resp = client.post("/webhooks/nomba", json=payload, headers=_signed_headers(payload))
    assert resp.status_code == 200
    event_id = resp.json()["id"]

    detail = client.get(f"/api/failures/{event_id}").json()
    # 7500.00 stored directly as naira — NOT divided by 100 (which
    # would have wrongly stored 75).
    assert detail["amount"] == 7500
    assert detail["has_contact"] is True


def test_checkout_url_is_same_path_structure_in_both_environments(monkeypatch):
    # CORRECTED (again): checkout does NOT use a different path prefix
    # per environment — a team member reproduced a real 404 hitting
    # /sandbox/checkout/order against the actual hackathon sandbox,
    # and the organizer's own "verified endpoints" reference lists
    # only a single form, /v1/checkout/order, for both. Only the HOST
    # differs between sandbox and production; _is_sandbox() is kept
    # only for anything that genuinely does need to branch (none of
    # the checkout functions do anymore).
    monkeypatch.setattr(settings, "NOMBA_API_BASE_URL", "https://sandbox.nomba.com/v1")
    assert nomba_client._is_sandbox() is True
    assert f"{settings.NOMBA_API_BASE_URL}/checkout/order" == "https://sandbox.nomba.com/v1/checkout/order"

    monkeypatch.setattr(settings, "NOMBA_API_BASE_URL", "https://api.nomba.com/v1")
    assert nomba_client._is_sandbox() is False
    assert f"{settings.NOMBA_API_BASE_URL}/checkout/order" == "https://api.nomba.com/v1/checkout/order"
