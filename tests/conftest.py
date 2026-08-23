"""Shared fixtures: isolated database per test, fake Razorpay client."""

import pytest

import app.db as db
import app.executor as executor
from app.db import get_conn, init_db, utcnow


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own throwaway SQLite file."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(executor, "REQUEST_GAP_SECONDS", 0)
    executor._LINK_QUOTA_EXHAUSTED[0] = False
    init_db()
    yield


class FakePaymentLink:
    """Stands in for razorpay.Client().payment_link — records calls."""

    def __init__(self, existing_items=None, create_error=None):
        self.created = []
        self.cancelled = []
        self.existing_items = existing_items or []
        self.create_error = create_error

    def create(self, payload):
        if self.create_error is not None:
            raise self.create_error
        self.created.append(payload)
        n = len(self.created)
        return {"id": f"plink_fake_{n}", "short_url": f"https://rzp.io/f{n}"}

    def all(self, query):
        return {"items": self.existing_items}

    def cancel(self, link_id):
        self.cancelled.append(link_id)


class FakeClient:
    def __init__(self, **kwargs):
        self.payment_link = FakePaymentLink(**kwargs)


@pytest.fixture
def fake_client():
    return FakeClient()


def seed_payment(pid="pay_t_0001", code="INSUFFICIENT_FUNDS",
                 status="failed", amount=50_000,
                 recovery_status="detected",
                 recommended_action="payment_link"):
    """Insert one payment (+ diagnosis) ready for the policy engine."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO payments (id, customer_name, customer_email, "
            "customer_phone, amount_paise, status, failure_code, "
            "failure_message, recovery_status, created_at) "
            "VALUES (?, 'Test Person', 't@example.com', '+919999999999', "
            "?, ?, ?, 'test failure', ?, ?)",
            (pid, amount, status, code, recovery_status, utcnow()),
        )
        conn.execute(
            "INSERT INTO diagnoses (payment_id, root_cause, transient, "
            "recommended_action, customer_message, model, created_at) "
            "VALUES (?, 'test cause', 1, ?, 'please pay', 'test-model', ?)",
            (pid, recommended_action, utcnow()),
        )
    return pid


def payment_state(pid):
    with get_conn() as conn:
        return conn.execute(
            "SELECT recovery_status FROM payments WHERE id = ?",
            (pid,)).fetchone()[0]


def action_rows(pid=None):
    with get_conn() as conn:
        if pid:
            return conn.execute(
                "SELECT * FROM recovery_actions WHERE payment_id = ? "
                "ORDER BY id", (pid,)).fetchall()
        return conn.execute(
            "SELECT * FROM recovery_actions ORDER BY id").fetchall()
