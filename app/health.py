"""Bank health, and why it changes what the agent does.

Roughly 40% of Indian payment failures are downstream bank problems rather
than anything about the customer. That matters here because attempts are a
BUDGET, not a free resource: policy allows three per payment, ever. Spending
one while the customer's bank is broken wastes a third of the budget on a
request that was never going to succeed, and burns a customer contact for
nothing.

So the agent asks a question before acting: is this bank healthy right now? If
a bank's recent attempts are failing far above the batch norm, its payments
are held rather than attempted, and the held attempt is not consumed.

This is deliberately NOT an outage detector competing with Razorpay's own
(theirs is better and sits closer to the network). It is the consumer side:
given that a bank looks unhealthy, don't spend budget on it.
"""

from app.db import get_conn, init_db

# A bank needs this many resolved attempts before its rate means anything.
MIN_SAMPLE = 4
# "Unhealthy" is RELATIVE to how the batch as a whole is doing. A fixed
# threshold is wrong in both directions: on a bad day every bank trips it, and
# on a good day a genuinely broken bank sits just under it. A bank is
# unhealthy when it is failing meaningfully worse than its peers right now.
UNHEALTHY_MULTIPLIER = 1.4
# ...but never flag a bank that is doing fine in absolute terms.
UNHEALTHY_FLOOR = 0.60
# How long to hold payments on an unhealthy bank before reconsidering.
HOLD_HOURS = 4


def bank_health(conn=None) -> dict:
    """Failure rate per bank across resolved recovery attempts."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute("""
            SELECT p.bank,
                   COUNT(*) attempts,
                   SUM(CASE WHEN ra.state = 'failed' THEN 1 ELSE 0 END) failed
            FROM recovery_actions ra
            JOIN payments p ON p.id = ra.payment_id
            WHERE ra.state IN ('succeeded', 'failed') AND p.bank IS NOT NULL
            GROUP BY p.bank""").fetchall()
    finally:
        if own:
            conn.close()

    total_attempts = sum(r["attempts"] for r in rows)
    total_failed = sum(r["failed"] for r in rows)
    batch_rate = total_failed / total_attempts if total_attempts else 0.0
    bar = max(UNHEALTHY_FLOOR, batch_rate * UNHEALTHY_MULTIPLIER)

    health = {}
    for r in rows:
        rate = r["failed"] / r["attempts"] if r["attempts"] else 0.0
        health[r["bank"]] = {
            "attempts": r["attempts"],
            "failed": r["failed"],
            "failure_rate": rate,
            "batch_rate": batch_rate,
            "bar": bar,
            "unhealthy": r["attempts"] >= MIN_SAMPLE and rate >= bar,
        }
    return health


def is_unhealthy(bank: str, health: dict) -> bool:
    entry = health.get(bank)
    return bool(entry and entry["unhealthy"])


def method_breakdown(conn=None) -> list:
    """Success rate per payment method — the number a blended rate hides.

    A merchant looking at "90% success" cannot see that UPI is at 94% while
    cards are at 78%. This is that view.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute("""
            SELECT method,
                   COUNT(*) total,
                   SUM(CASE WHEN status = 'captured' THEN 1 ELSE 0 END) paid,
                   SUM(CASE WHEN recovery_status = 'recovered'
                            THEN 1 ELSE 0 END) recovered,
                   COALESCE(SUM(CASE WHEN status != 'captured'
                            THEN amount_paise ELSE 0 END), 0) at_risk
            FROM payments WHERE method IS NOT NULL
            GROUP BY method ORDER BY total DESC""").fetchall()
    finally:
        if own:
            conn.close()

    out = []
    for r in rows:
        first_pass = r["paid"] / r["total"] if r["total"] else 0
        after = ((r["paid"] + r["recovered"]) / r["total"]
                 if r["total"] else 0)
        out.append({
            "method": r["method"],
            "total": r["total"],
            "first_pass_rate": first_pass,
            "rate_after_recovery": after,
            "recovered_n": r["recovered"],
            "at_risk_paise": r["at_risk"],
        })
    return out


if __name__ == "__main__":
    init_db()
    print("Success rate by payment method")
    print(f"{'method':<12}{'payments':>9}{'first try':>11}"
          f"{'after agent':>13}{'lift':>8}")
    for m in method_breakdown():
        lift = m["rate_after_recovery"] - m["first_pass_rate"]
        print(f"{m['method']:<12}{m['total']:>9}"
              f"{m['first_pass_rate'] * 100:>10.0f}%"
              f"{m['rate_after_recovery'] * 100:>12.0f}%"
              f"{lift * 100:>7.0f}%")

    health = bank_health()
    if health:
        any_h = next(iter(health.values()))
        print(f"\nBank health — batch norm {any_h['batch_rate'] * 100:.0f}% "
              f"failing, flagged above {any_h['bar'] * 100:.0f}%")
        for bank, h in sorted(health.items(),
                              key=lambda kv: -kv[1]["failure_rate"]):
            flag = "  <-- UNHEALTHY, payments held" if h["unhealthy"] else ""
            print(f"  {bank:<10} {h['failed']}/{h['attempts']} failed "
                  f"({h['failure_rate'] * 100:.0f}%){flag}")
