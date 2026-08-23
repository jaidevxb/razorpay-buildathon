"""Channel assignment and bank-health decisions."""

from app.channels import assign
from app.db import get_conn, utcnow
from app.health import (MIN_SAMPLE, bank_health, is_unhealthy,
                        method_breakdown)
from tests.conftest import seed_payment


def test_assignment_is_stable_for_a_payment_id():
    """Backfilled rows must get what a fresh seed would have given them."""
    a = assign("pay_sim_0042", "INSUFFICIENT_FUNDS")
    b = assign("pay_sim_0042", "INSUFFICIENT_FUNDS")
    assert a == b


def test_assignment_respects_what_the_failure_implies():
    for _ in range(20):
        assert assign(f"p{_}", "UPI_TIMEOUT")[0] == "upi"
        assert assign(f"p{_}", "EXPIRED_CARD")[0] == "card"


def test_wallets_are_not_given_a_bank():
    method, holder = assign("pay_w", None)
    if method == "wallet":
        assert holder in ("Paytm", "PhonePe", "Amazon Pay")


def _attempts(pid, bank, n_failed, n_ok):
    with get_conn() as conn:
        conn.execute("UPDATE payments SET bank = ? WHERE id = ?", (bank, pid))
        for i in range(n_failed + n_ok):
            conn.execute(
                "INSERT INTO recovery_actions (payment_id, action, attempt, "
                "idempotency_key, state, created_at) VALUES "
                "(?, 'retry', ?, ?, ?, ?)",
                (pid, i + 1, f"{pid}:{bank}:{i}",
                 "failed" if i < n_failed else "succeeded", utcnow()),
            )


def test_a_bank_failing_far_above_the_norm_is_flagged():
    good = seed_payment(pid="pay_good")
    bad = seed_payment(pid="pay_bad")
    _attempts(good, "Kotak", n_failed=1, n_ok=9)     # 10% failing
    _attempts(bad, "SBI", n_failed=9, n_ok=1)        # 90% failing
    health = bank_health()
    assert is_unhealthy("SBI", health)
    assert not is_unhealthy("Kotak", health)


def test_a_bad_day_for_everyone_flags_nobody():
    """A fixed threshold would flag every bank here. A relative one shouldn't."""
    a = seed_payment(pid="pay_a")
    b = seed_payment(pid="pay_b")
    _attempts(a, "HDFC", n_failed=7, n_ok=3)
    _attempts(b, "ICICI", n_failed=7, n_ok=3)
    health = bank_health()
    assert not is_unhealthy("HDFC", health)
    assert not is_unhealthy("ICICI", health)


def test_a_tiny_sample_is_never_flagged():
    pid = seed_payment(pid="pay_small")
    _attempts(pid, "PNB", n_failed=MIN_SAMPLE - 1, n_ok=0)
    assert not is_unhealthy("PNB", bank_health())


def test_method_breakdown_separates_first_pass_from_after_recovery():
    seed_payment(pid="pay_m1", code="EXPIRED_CARD",
                 recovery_status="recovered")
    seed_payment(pid="pay_m2", code="EXPIRED_CARD",
                 recovery_status="detected")
    rows = {r["method"]: r for r in method_breakdown()}
    card = rows["card"]
    assert card["total"] == 2
    assert card["first_pass_rate"] == 0.0      # both failed at checkout
    assert card["rate_after_recovery"] == 0.5  # one won back
