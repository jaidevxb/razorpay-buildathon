"""Idempotency, crash recovery, stopping rules, graceful degradation."""

import razorpay

import app.executor as executor
from app import policy
from app.db import get_conn
from tests.conftest import (FakeClient, action_rows, payment_state,
                            seed_payment)


def _plan(pid_kwargs=None):
    seed_payment(**(pid_kwargs or {}))
    policy.plan()


def test_successful_action_recovers_payment(monkeypatch, fake_client):
    _plan()
    monkeypatch.setattr(executor, "_simulated_outcome", lambda a, p: True)
    handled = executor.run_pass(fake_client, force_due=True)
    assert handled == 1
    assert payment_state("pay_t_0001") == "recovered"


def test_completed_actions_are_never_rerun(monkeypatch, fake_client):
    _plan()
    monkeypatch.setattr(executor, "_simulated_outcome", lambda a, p: True)
    executor.run_pass(fake_client, force_due=True)
    assert executor.run_pass(fake_client, force_due=True) == 0
    assert len(fake_client.payment_link.created) == 1


def test_attempts_stop_at_max_then_escalate(monkeypatch, fake_client):
    _plan()
    monkeypatch.setattr(executor, "_simulated_outcome", lambda a, p: False)
    monkeypatch.setattr(executor, "_maybe_customer_reply", lambda a: None)
    for _ in range(10):  # loop far beyond the cap; it must not run away
        if executor.run_pass(fake_client, force_due=True) == 0:
            break
    assert len(action_rows("pay_t_0001")) == policy.MAX_ATTEMPTS
    assert payment_state("pay_t_0001") == "escalated"


def test_crash_reconciliation_adopts_existing_link(monkeypatch):
    _plan()
    # simulate a crash: action is stuck in 'executing', and the link it was
    # creating DOES exist on Razorpay's side
    with get_conn() as conn:
        conn.execute("UPDATE recovery_actions SET state = 'executing'")
    client = FakeClient(existing_items=[
        {"id": "plink_preexisting", "short_url": "https://rzp.io/pre"}])
    monkeypatch.setattr(executor, "_simulated_outcome", lambda a, p: True)
    executor.run_pass(client, force_due=True)
    actions = action_rows("pay_t_0001")
    assert actions[0]["rzp_link_id"] == "plink_preexisting"
    assert client.payment_link.created == []   # adopted, never re-created


def test_customer_reply_pauses_retries(monkeypatch, fake_client):
    _plan()
    monkeypatch.setattr(executor, "_simulated_outcome", lambda a, p: False)
    monkeypatch.setattr(executor, "_maybe_customer_reply",
                        lambda a: "will pay in 2 days, thoda busy hu abhi")
    executor.run_pass(fake_client, force_due=True)
    assert payment_state("pay_t_0001") == "promised"
    assert len(action_rows("pay_t_0001")) == 1   # no attempt 2 while promised
    with get_conn() as conn:
        promises = conn.execute("SELECT * FROM promises").fetchall()
    assert len(promises) == 1


def test_link_quota_exhaustion_degrades_instead_of_halting(monkeypatch):
    _plan()
    quota_error = razorpay.errors.ServerError(
        "test mode limit of 30 reached for payment_link")
    client = FakeClient(create_error=quota_error)
    monkeypatch.setattr(executor, "_simulated_outcome", lambda a, p: True)
    handled = executor.run_pass(client, force_due=True)
    assert handled == 1                                # batch continued
    assert payment_state("pay_t_0001") == "recovered"
    assert executor._LINK_QUOTA_EXHAUSTED[0] is True   # and remembers


def test_scheduled_actions_wait_for_their_time(monkeypatch, fake_client):
    # a GATEWAY_ERROR retry is scheduled hours out; without force_due the
    # executor must leave it alone
    seed_payment(code="GATEWAY_ERROR", recommended_action="retry")
    policy.plan()
    monkeypatch.setattr(executor, "_simulated_outcome", lambda a, p: True)
    assert executor.run_pass(fake_client, force_due=False) == 0
    assert executor.run_pass(fake_client, force_due=True) == 1
