"""A settled payment stays settled, and fraud has no side door.

These cover a bug found by auditing the running system: the reply channel
wrote payment state with no check on what that state already was. A reply
arriving after a payment was settled reopened it — and because the promise
tracker never looked at the failure class, a FRAUD_SUSPECTED payment that the
policy engine had refused to touch could be pulled back into the recovery
pipeline by an ordinary-looking "I'll pay Friday". See CHALLENGES.md entry 8.
"""

import pytest

import app.promises as promises
from app.db import get_conn, utcnow
from tests.conftest import payment_state, seed_payment

TERMINAL = ["recovered", "recovered_manual", "written_off"]


def _add_reply(pid, text="will pay in 2 days", status="received",
               due_offset=-1, intent=None):
    from datetime import datetime, timedelta, timezone
    due = (datetime.now(timezone.utc)
           + timedelta(days=due_offset)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO promises (payment_id, raw_reply, intent, due_at, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, text, intent, due, status, utcnow()),
        )


@pytest.mark.parametrize("state", TERMINAL)
def test_settled_payments_are_not_reopened_by_a_late_reply(state):
    pid = seed_payment(recovery_status=state)
    _add_reply(pid)
    with get_conn() as conn:
        assert promises._blocked_reason(conn, pid) is not None
    assert payment_state(pid) == state


@pytest.mark.parametrize("state", TERMINAL)
def test_settled_payments_are_not_reopened_by_a_due_promise(state):
    pid = seed_payment(recovery_status=state)
    _add_reply(pid, status="pending", intent="promise_to_pay")
    promises.resolve_due(force=True)
    assert payment_state(pid) == state
    with get_conn() as conn:
        assert conn.execute(
            "SELECT status FROM promises").fetchone()["status"] == "closed"


def test_fraud_cannot_be_recovered_through_the_reply_channel():
    """The flagship safety rule must hold on every route into the pipeline."""
    pid = seed_payment(code="FRAUD_SUSPECTED", recovery_status="escalated")
    _add_reply(pid, "haan bhai Friday ko pakka kar dunga",
               status="pending", intent="promise_to_pay")
    promises.resolve_due(force=True)
    assert payment_state(pid) == "escalated"
    assert payment_state(pid) != "recovered"


def test_fraud_reply_is_filed_but_never_acted_on():
    pid = seed_payment(code="FRAUD_SUSPECTED", recovery_status="escalated")
    with get_conn() as conn:
        reason = promises._blocked_reason(conn, pid)
    assert reason is not None and "FRAUD_SUSPECTED" in reason


def test_an_open_payment_is_still_actionable():
    """The guard must not block ordinary work."""
    pid = seed_payment(recovery_status="in_progress")
    with get_conn() as conn:
        assert promises._blocked_reason(conn, pid) is None


def test_missing_payment_is_blocked_not_crashed():
    with get_conn() as conn:
        assert promises._blocked_reason(conn, "pay_does_not_exist") is not None
