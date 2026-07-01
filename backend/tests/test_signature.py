import hashlib
import hmac
import json

import pytest

from app.services.signature import (
    verify_signature,
    extract_event,
    SignatureVerificationError,
)

FAKE_SECRET = "test_webhook_secret_123"

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

# Encoded once, reused everywhere — signature must be computed over
# these EXACT bytes, matching how FastAPI's request.body() would hand
# them to us before any JSON parsing happens.
RAW_BODY = json.dumps(SAMPLE_PAYLOAD).encode("utf-8")


def _sign(raw_body: bytes, secret: str = FAKE_SECRET) -> str:
    return hmac.new(key=secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256).hexdigest()


def test_signature_matches_nomba_documented_scheme():
    # Hand-computed independently of the implementation under test,
    # confirming HMAC-SHA256 over the raw body — exactly Nomba's own
    # documented Node.js sample (crypto.createHmac('sha256', secret)
    # .update(req.body).digest('hex')).
    real_expected = hmac.new(FAKE_SECRET.encode(), RAW_BODY, hashlib.sha256).hexdigest()
    assert _sign(RAW_BODY) == real_expected


def test_verify_accepts_valid_signature():
    sig = _sign(RAW_BODY)
    headers = {"nomba-signature": sig}
    # Should not raise.
    verify_signature(RAW_BODY, headers, FAKE_SECRET)


def test_verify_is_case_insensitive_on_header_name():
    sig = _sign(RAW_BODY)
    headers = {"Nomba-Signature": sig}
    verify_signature(RAW_BODY, headers, FAKE_SECRET)


def test_verify_rejects_tampered_body():
    sig = _sign(RAW_BODY)
    headers = {"nomba-signature": sig}

    tampered_body = json.dumps({**SAMPLE_PAYLOAD, "event_type": "payment_success"}).encode("utf-8")

    with pytest.raises(SignatureVerificationError):
        verify_signature(tampered_body, headers, FAKE_SECRET)


def test_verify_rejects_missing_signature_header():
    with pytest.raises(SignatureVerificationError):
        verify_signature(RAW_BODY, {}, FAKE_SECRET)


def test_verify_rejects_wrong_secret():
    sig = _sign(RAW_BODY, secret="the_real_secret")
    headers = {"nomba-signature": sig}
    with pytest.raises(SignatureVerificationError):
        verify_signature(RAW_BODY, headers, "a_different_secret")


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
