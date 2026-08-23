"""The invariants that make the agent safe to point at money."""

from datetime import datetime, timezone

from app import policy
from tests.conftest import action_rows, payment_state, seed_payment


def test_fraud_is_never_recovered_even_if_llm_says_retry():
    pid = seed_payment(code="FRAUD_SUSPECTED", recommended_action="retry")
    policy.plan()
    assert payment_state(pid) == "escalated"
    assert action_rows(pid) == []          # no recovery action exists at all


def test_amounts_above_cap_require_a_human():
    pid = seed_payment(amount=policy.AMOUNT_CAP_PAISE + 1)
    policy.plan()
    assert payment_state(pid) == "escalated"
    assert action_rows(pid) == []


def test_llm_action_outside_allowed_set_is_overridden():
    # 'retry' is pointless for a dead card; policy must override to update_card
    pid = seed_payment(code="EXPIRED_CARD", recommended_action="retry")
    stats = policy.plan()
    actions = action_rows(pid)
    assert len(actions) == 1
    assert actions[0]["action"] == "update_card"
    assert stats["llm_overridden"] == 1


def test_unknown_failure_class_escalates_not_guesses():
    pid = seed_payment(code="SOMETHING_NEW_AND_WEIRD")
    policy.plan()
    assert payment_state(pid) == "escalated"


def test_planning_twice_never_duplicates_actions():
    pid = seed_payment()
    policy.plan()
    # payment is now in_progress, so a second plan() skips it; even if it
    # were re-run on 'detected', the idempotency key blocks a duplicate row
    policy.plan()
    assert len(action_rows(pid)) == 1


def test_insufficient_funds_retry_waits_for_salary_day():
    when, why = policy.schedule_for("retry", "INSUFFICIENT_FUNDS", 1)
    scheduled = datetime.fromisoformat(when)
    assert scheduled.day == 1                     # 1st of next month
    assert scheduled > datetime.now(timezone.utc)
    assert "salary" in why


def test_first_link_goes_out_immediately_but_reminders_wait():
    first, _ = policy.schedule_for("payment_link", "INSUFFICIENT_FUNDS", 1)
    second, why = policy.schedule_for("payment_link", "INSUFFICIENT_FUNDS", 2)
    assert first < second
    assert "spam" in why
