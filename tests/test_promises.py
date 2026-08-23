"""Promise policy: listening to customers is bounded too."""

from datetime import datetime, timedelta, timezone

import app.promises as promises
from app.db import get_conn, utcnow
from tests.conftest import payment_state, seed_payment


def _make_promise(pid, status="pending", due_offset_days=-1):
    due = (datetime.now(timezone.utc)
           + timedelta(days=due_offset_days)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO promises (payment_id, raw_reply, intent, due_at, "
            "status, created_at) VALUES (?, 'test reply', 'promise_to_pay', "
            "?, ?, ?)", (pid, due, status, utcnow()),
        )
        conn.execute(
            "UPDATE payments SET recovery_status = 'promised' WHERE id = ?",
            (pid,),
        )


def test_kept_promise_recovers_payment(monkeypatch):
    pid = seed_payment()
    _make_promise(pid)
    monkeypatch.setattr(promises, "KEPT_PROBABILITY", 1.0)
    promises.resolve_due()
    assert payment_state(pid) == "recovered"


def test_broken_promise_goes_to_a_human_not_another_loop(monkeypatch):
    pid = seed_payment()
    _make_promise(pid)
    monkeypatch.setattr(promises, "KEPT_PROBABILITY", 0.0)
    promises.resolve_due()
    assert payment_state(pid) == "escalated"


def test_future_promises_are_left_alone_until_due():
    pid = seed_payment()
    _make_promise(pid, due_offset_days=+3)
    assert promises.resolve_due() == 0
    assert payment_state(pid) == "promised"
    assert promises.resolve_due(force=True) == 1   # demo pacing override


def test_refusal_stops_contact_immediately():
    pid = seed_payment()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO promises (payment_id, raw_reply, status, created_at) "
            "VALUES (?, 'not interested, cancel my order', 'received', ?)",
            (pid, utcnow()),
        )
        promises._close(conn, 1, "refusal", "written_off", pid,
                        "customer refused")
    assert payment_state(pid) == "written_off"
