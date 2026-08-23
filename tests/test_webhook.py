"""Webhook ingestion: signature verification is the gate."""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import payment_state, seed_payment
from app.db import get_conn, utcnow

client = TestClient(app)
SECRET = "whsec_test_secret"


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _post(event: dict, signature: str | None = None):
    body = json.dumps(event).encode()
    return client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": signature or _sign(body),
                 "Content-Type": "application/json"},
    )


FAILED_EVENT = {
    "event": "payment.failed",
    "payload": {"payment": {"entity": {
        "id": "pay_webhook_001", "order_id": "order_wh_1",
        "email": "c@example.com", "contact": "+919888877776",
        "amount": 123400, "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Payment failed due to insufficient funds",
        "notes": {"name": "Webhook Customer"},
    }}},
}


def test_valid_signature_ingests_failed_payment(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    r = _post(FAILED_EVENT)
    assert r.status_code == 200 and r.json()["ingested"] is True
    assert payment_state("pay_webhook_001") == "detected"


def test_bad_signature_is_rejected_and_nothing_is_written(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    r = _post(FAILED_EVENT, signature="0" * 64)
    assert r.status_code == 401
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0


def test_missing_secret_refuses_service(monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    assert _post(FAILED_EVENT).status_code == 503


def test_duplicate_webhook_delivery_is_idempotent(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    _post(FAILED_EVENT)
    r = _post(FAILED_EVENT)   # Razorpay retries deliveries; must not duplicate
    assert r.json()["ingested"] is False
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1


def test_link_paid_marks_payment_recovered(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    pid = seed_payment(recovery_status="in_progress")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO recovery_actions (payment_id, action, attempt, "
            "idempotency_key, created_at) VALUES (?, 'payment_link', 1, "
            "?, ?)", (pid, f"{pid}:payment_link:1", utcnow()),
        )
    event = {"event": "payment_link.paid",
             "payload": {"payment_link": {"entity": {
                 "reference_id": f"{pid}:payment_link:1"}}}}
    r = _post(event)
    assert r.status_code == 200 and r.json()["payment"] == pid
    assert payment_state(pid) == "recovered"
