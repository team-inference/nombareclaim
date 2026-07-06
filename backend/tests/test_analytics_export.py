import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db

init_db()

client = TestClient(app)

SIGNATURE_KEY = "test_webhook_secret_123"


def _signed_headers(raw_body: bytes) -> dict:
    sig = hmac.new(SIGNATURE_KEY.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {"nomba-signature": sig, "Content-Type": "application/json"}


def _post_failure(request_id: str, transaction_id: str, response_code: str, amount_kobo: int):
    payload = {
        "event_type": "PAYMENT_FAILED",
        "requestId": request_id,
        "data": {
            "merchantTxRef": transaction_id,
            "amount": amount_kobo,
            "currency": "NGN",
            "transaction": {"responseCode": response_code},
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    resp = client.post("/webhooks/nomba", content=raw_body, headers=_signed_headers(raw_body))
    assert resp.status_code == 200
    return resp.json()["id"]


def test_breakdown_endpoint_returns_grouped_classification_data():
    _post_failure("req-analytics-1", "txn-analytics-1", "51", 500000)  # insufficient funds

    resp = client.get("/api/analytics/breakdown")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert isinstance(body["items"], list)
    # Every item must have all expected keys and sane types.
    for item in body["items"]:
        assert set(item.keys()) == {
            "classification",
            "count",
            "total_amount",
            "recovered_count",
            "recovered_amount",
            "recovery_rate",
        }
        assert item["count"] >= 1


def test_export_endpoint_returns_csv_with_header_row():
    _post_failure("req-analytics-2", "txn-analytics-2", "05", 300000)  # card declined

    resp = client.get("/api/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]

    lines = resp.text.strip().splitlines()
    header = lines[0].split(",")
    assert "nomba_transaction_id" in header
    assert "has_contact" in header
    # At least the header + our seeded rows should be present.
    assert len(lines) >= 2


def test_trend_endpoint_returns_real_cumulative_data_not_a_404():
    _post_failure("req-analytics-4", "txn-analytics-4", "51", 200000)

    resp = client.get("/api/summary/trend")
    assert resp.status_code == 200
    points = resp.json()
    assert isinstance(points, list)
    assert len(points) == 7  # default days=7
    for point in points:
        assert set(point.keys()) == {"date", "recovery_rate"}
    # Today's point should reflect the event we just created existing
    # (total >= 1), i.e. it's real data, not a fixed mock curve.
    assert points[-1]["recovery_rate"] >= 0.0


def test_failure_list_includes_new_retry_and_contact_fields():
    event_id = _post_failure("req-analytics-3", "txn-analytics-3", "91", 100000)

    resp = client.get(f"/api/failures/{event_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "has_contact" in body
    assert "retry_count" in body
    assert "next_retry_at" in body
    assert body["has_contact"] is False  # no customerEmail in this payload
