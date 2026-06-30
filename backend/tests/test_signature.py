import hashlib
import hmac
import time

import pytest

from app.services.signature import (
    verify_nomba_signature,
    compute_signature,
    build_signing_string,
    SignatureVerificationError,
)

FAKE_SECRET = "test_signature_key_123"

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


def test_signing_string_field_order():
    s = build_signing_string(SAMPLE_PAYLOAD, "1719739200")
    assert s == (
        "payment_failed:req-abc-123:user-1:wallet-1:txn-1:"
        "vact_transfer:2026-06-30T10:00:00Z:51:1719739200"
    )


def test_compute_signature_matches_hand_computed_hmac():
    timestamp = "1719739200"
    message = build_signing_string(SAMPLE_PAYLOAD, timestamp)

    # Hand-computed using Python's stdlib hmac directly, independent of
    # the implementation under test, with the same fake secret.
    expected = hmac.new(
        key=FAKE_SECRET.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    actual = compute_signature(SAMPLE_PAYLOAD, timestamp, FAKE_SECRET)
    assert actual == expected


def test_verify_accepts_valid_signature():
    timestamp = str(int(time.time()))
    sig = compute_signature(SAMPLE_PAYLOAD, timestamp, FAKE_SECRET)
    headers = {"signature": sig, "timestamp": timestamp}

    verified = verify_nomba_signature(
        SAMPLE_PAYLOAD, headers, FAKE_SECRET, "signature", "timestamp", 300
    )
    assert verified.event_type == "payment_failed"
    assert verified.transaction_id == "txn-1"


def test_verify_rejects_tampered_payload():
    timestamp = str(int(time.time()))
    sig = compute_signature(SAMPLE_PAYLOAD, timestamp, FAKE_SECRET)
    headers = {"signature": sig, "timestamp": timestamp}

    tampered = dict(SAMPLE_PAYLOAD)
    tampered["data"] = dict(SAMPLE_PAYLOAD["data"])
    tampered["data"]["transaction"] = dict(SAMPLE_PAYLOAD["data"]["transaction"])
    # transactionId IS one of the 8 fields in the signed string (unlike
    # amount, which Nomba's documented field list does NOT include —
    # see the note in services/signature.py about what this means for
    # amount-tampering risk on the recovery flow).
    tampered["data"]["transaction"]["transactionId"] = "txn-attacker-substituted"

    with pytest.raises(SignatureVerificationError):
        verify_nomba_signature(tampered, headers, FAKE_SECRET, "signature", "timestamp", 300)


def test_verify_rejects_missing_signature_header():
    timestamp = str(int(time.time()))
    headers = {"timestamp": timestamp}
    with pytest.raises(SignatureVerificationError):
        verify_nomba_signature(SAMPLE_PAYLOAD, headers, FAKE_SECRET, "signature", "timestamp", 300)


def test_verify_rejects_stale_timestamp():
    old_timestamp = str(int(time.time()) - 3600)  # 1 hour old
    sig = compute_signature(SAMPLE_PAYLOAD, old_timestamp, FAKE_SECRET)
    headers = {"signature": sig, "timestamp": old_timestamp}

    with pytest.raises(SignatureVerificationError):
        verify_nomba_signature(SAMPLE_PAYLOAD, headers, FAKE_SECRET, "signature", "timestamp", 300)
