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
FAKE_TIMESTAMP = "2026-06-30T10:00:00Z"

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


def _sign(payload: dict, timestamp: str = FAKE_TIMESTAMP, secret: str = FAKE_SECRET) -> str:
    """Independent reference implementation, hand-built from Nomba's own
    documented reference code (the Python tab of
    developer.nomba.com/docs/api-basics/webhook), not from the
    implementation under test."""
    data = payload.get("data", {})
    merchant = data.get("merchant", {})
    transaction = data.get("transaction", {})

    response_code = transaction.get("responseCode", "")
    if response_code is None or str(response_code).lower() == "null":
        response_code = ""

    hashing_payload = (
        f"{payload.get('event_type', '')}:{payload.get('requestId', '')}:"
        f"{merchant.get('userId', '')}:{merchant.get('walletId', '')}:"
        f"{transaction.get('transactionId', '')}:{transaction.get('type', '')}:"
        f"{transaction.get('time', '')}:{response_code}:{timestamp}"
    )
    digest = hmac.new(secret.encode("utf-8"), hashing_payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _headers(sig: str, timestamp: str = FAKE_TIMESTAMP) -> dict:
    return {"nomba-signature": sig, "nomba-timestamp": timestamp}


def test_signature_matches_nomba_documented_scheme_exactly():
    """This is the single most important test in this file: it proves
    our implementation produces byte-for-byte the same signature as an
    independent hand-rolled reference built directly from Nomba's own
    published sample code, not copy-pasted from signature.py."""
    from app.services import signature as sig_module

    normalize = sig_module._normalize_response_code
    assert normalize("null") == ""
    assert normalize(None) == ""
    assert normalize("51") == "51"

    expected = _sign(SAMPLE_PAYLOAD)
    # verify_signature doesn't return the signature directly, so prove
    # equivalence by confirming a signature built the same way passes.
    verify_signature(SAMPLE_PAYLOAD, _headers(expected), FAKE_SECRET)


def test_verify_accepts_valid_signature():
    sig = _sign(SAMPLE_PAYLOAD)
    verify_signature(SAMPLE_PAYLOAD, _headers(sig), FAKE_SECRET)


def test_verify_is_case_insensitive_on_header_name():
    sig = _sign(SAMPLE_PAYLOAD)
    headers = {"Nomba-Signature": sig, "Nomba-Timestamp": FAKE_TIMESTAMP}
    verify_signature(SAMPLE_PAYLOAD, headers, FAKE_SECRET)


def test_verify_falls_back_to_nomba_sig_value_header():
    sig = _sign(SAMPLE_PAYLOAD)
    headers = {"nomba-sig-value": sig, "nomba-timestamp": FAKE_TIMESTAMP}
    verify_signature(SAMPLE_PAYLOAD, headers, FAKE_SECRET)


def test_verify_rejects_tampered_payload():
    sig = _sign(SAMPLE_PAYLOAD)
    tampered = {**SAMPLE_PAYLOAD, "event_type": "payment_success"}
    with pytest.raises(SignatureVerificationError):
        verify_signature(tampered, _headers(sig), FAKE_SECRET)


def test_verify_rejects_wrong_timestamp():
    # The timestamp is a hash INPUT, not just metadata — a signature
    # computed for one timestamp must not validate against another,
    # since the header value used in the hash differs.
    sig = _sign(SAMPLE_PAYLOAD, timestamp=FAKE_TIMESTAMP)
    with pytest.raises(SignatureVerificationError):
        verify_signature(SAMPLE_PAYLOAD, _headers(sig, timestamp="2099-01-01T00:00:00Z"), FAKE_SECRET)


def test_verify_rejects_missing_signature_header():
    with pytest.raises(SignatureVerificationError):
        verify_signature(SAMPLE_PAYLOAD, {"nomba-timestamp": FAKE_TIMESTAMP}, FAKE_SECRET)


def test_verify_rejects_missing_timestamp_header():
    sig = _sign(SAMPLE_PAYLOAD)
    with pytest.raises(SignatureVerificationError):
        verify_signature(SAMPLE_PAYLOAD, {"nomba-signature": sig}, FAKE_SECRET)


def test_verify_rejects_wrong_secret():
    sig = _sign(SAMPLE_PAYLOAD, secret="the_real_secret")
    with pytest.raises(SignatureVerificationError):
        verify_signature(SAMPLE_PAYLOAD, _headers(sig), "a_different_secret")


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
