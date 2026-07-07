import base64
import hashlib
import hmac

import pytest

from app.services.signature import (
    verify_signature,
    extract_event,
    SignatureVerificationError,
)

FAKE_SECRET = "test_webhook_secret_123"
TIMESTAMP = "1751500000"

SAMPLE_PAYLOAD = {
    "event_type": "payment_failed",
    "requestId": "req-abc-123",
    "data": {
        "merchant": {
            "userId": "user-1",
            "walletId": "wallet-1",
        },
        "transaction": {
            "transactionId": "txn-1",
            "type": "vact_transfer",
            "time": "2026-06-30T10:00:00Z",
            "responseCode": "51",
            "amount": "15000.00",
            "currency": "NGN",
        },
    },
}


def _sign(payload: dict, timestamp: str, secret: str = FAKE_SECRET) -> str:
    """Hand-computed independently of the implementation under test,
    matching Nomba's real documented scheme: HMAC-SHA256, Base64
    encoded, over event_type:requestId:userId:walletId:transactionId:
    type:time:responseCode:timestamp."""
    data = payload.get("data", {})
    merchant = data.get("merchant", {})
    transaction = data.get("transaction", {})
    signing_string = ":".join(
        [
            payload.get("event_type", ""),
            payload.get("requestId", ""),
            merchant.get("userId", ""),
            merchant.get("walletId", ""),
            transaction.get("transactionId", ""),
            transaction.get("type", ""),
            transaction.get("time", ""),
            transaction.get("responseCode", ""),
            timestamp,
        ]
    )
    digest = hmac.new(secret.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_verify_accepts_valid_signature():
    sig = _sign(SAMPLE_PAYLOAD, TIMESTAMP)
    headers = {"nomba-signature": sig, "nomba-timestamp": TIMESTAMP}
    # Should not raise.
    verify_signature(SAMPLE_PAYLOAD, headers, FAKE_SECRET)


def test_verify_is_case_insensitive_on_header_name():
    sig = _sign(SAMPLE_PAYLOAD, TIMESTAMP)
    headers = {"Nomba-Signature": sig, "Nomba-Timestamp": TIMESTAMP}
    verify_signature(SAMPLE_PAYLOAD, headers, FAKE_SECRET)


def test_verify_rejects_tampered_payload():
    sig = _sign(SAMPLE_PAYLOAD, TIMESTAMP)
    headers = {"nomba-signature": sig, "nomba-timestamp": TIMESTAMP}

    tampered_payload = {**SAMPLE_PAYLOAD, "event_type": "payment_success"}

    with pytest.raises(SignatureVerificationError):
        verify_signature(tampered_payload, headers, FAKE_SECRET)


def test_verify_rejects_missing_signature_header():
    with pytest.raises(SignatureVerificationError):
        verify_signature(SAMPLE_PAYLOAD, {"nomba-timestamp": TIMESTAMP}, FAKE_SECRET)


def test_verify_rejects_missing_timestamp_header():
    # The timestamp is one of the signed fields, not just a freshness
    # check — a request missing it can never produce a valid signature.
    sig = _sign(SAMPLE_PAYLOAD, TIMESTAMP)
    with pytest.raises(SignatureVerificationError):
        verify_signature(SAMPLE_PAYLOAD, {"nomba-signature": sig}, FAKE_SECRET)


def test_verify_rejects_wrong_secret():
    sig = _sign(SAMPLE_PAYLOAD, TIMESTAMP, secret="the_real_secret")
    headers = {"nomba-signature": sig, "nomba-timestamp": TIMESTAMP}
    with pytest.raises(SignatureVerificationError):
        verify_signature(SAMPLE_PAYLOAD, headers, "a_different_secret")


def test_verify_rejects_different_timestamp_than_signed():
    # Timestamp is hashed input, not just echoed — signing with one
    # timestamp then verifying against a different one must fail.
    sig = _sign(SAMPLE_PAYLOAD, TIMESTAMP)
    headers = {"nomba-signature": sig, "nomba-timestamp": "1751500001"}
    with pytest.raises(SignatureVerificationError):
        verify_signature(SAMPLE_PAYLOAD, headers, FAKE_SECRET)


def test_extract_event_pulls_expected_fields():
    event = extract_event(SAMPLE_PAYLOAD)
    assert event.event_type == "payment_failed"
    assert event.request_id == "req-abc-123"
    assert event.transaction_id == "txn-1"
    assert event.response_code == "51"
    assert event.merchant_user_id == "user-1"
    assert event.wallet_id == "wallet-1"


def test_extract_event_tolerates_missing_fields():
    event = extract_event({"event_type": "payment_failed"})
    assert event.event_type == "payment_failed"
    assert event.transaction_id is None
    assert event.response_code is None
