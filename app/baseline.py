"""Baseline comparison: the agent vs. what merchants actually do today.

The common merchant "strategy" is blind auto-retry: hit every failed payment
again up to 3 times, immediately, with no diagnosis, no timing, no listening.
This module simulates that strategy over the SAME batch (same payments, seeded
RNG, so the comparison is reproducible) and scores both sides.

Assumptions are disclosed in BLIND_RETRY_PROB: an immediate, undiagnosed,
channel-inappropriate retry succeeds less often than a diagnosed, well-timed,
channel-appropriate action — and it "succeeds" on suspected-fraud payments,
which is not a win, it's a chargeback time bomb. Those numbers are stated,
not hidden.

Run:  python -m app.baseline
"""

import random

from app.db import get_conn, init_db

# Success probability of an immediate blind charge retry, per failure class.
BLIND_RETRY_PROB = {
    "INSUFFICIENT_FUNDS": 0.12,  # the account is still empty minutes later
    "GATEWAY_ERROR": 0.55,       # the outage often persists through the retry
    "UPI_TIMEOUT": 0.45,         # the customer already walked away
    "EXPIRED_CARD": 0.00,        # a dead card stays dead, forever
    "CHECKOUT_ABANDONED": 0.05,  # there is no authorization to retry
    "FRAUD_SUSPECTED": 0.25,     # it may go through — that's the problem
}
BLIND_MAX_ATTEMPTS = 3


def simulate_blind_retry() -> dict:
    """Run the blind-retry strategy over the current batch (no DB writes)."""
    init_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, amount_paise, failure_code FROM payments "
            "WHERE status != 'captured'").fetchall()
    finally:
        conn.close()

    recovered = fraud_recovered = 0
    attempts = fraud_attempts = dead_card_attempts = 0
    recovered_n = 0
    for r in rows:
        prob = BLIND_RETRY_PROB.get(r["failure_code"], 0.1)
        for attempt in range(1, BLIND_MAX_ATTEMPTS + 1):
            attempts += 1
            if r["failure_code"] == "FRAUD_SUSPECTED":
                fraud_attempts += 1
            if r["failure_code"] == "EXPIRED_CARD":
                dead_card_attempts += 1
            rng = random.Random(f"baseline:{r['id']}:{attempt}")
            if rng.random() < prob:
                if r["failure_code"] == "FRAUD_SUSPECTED":
                    fraud_recovered += r["amount_paise"]
                else:
                    recovered += r["amount_paise"]
                recovered_n += 1
                break

    return {
        "recovered_paise": recovered,
        "recovered_n": recovered_n,
        "fraud_recovered_paise": fraud_recovered,
        "attempts": attempts,
        "fraud_retries": fraud_attempts,
        # For blind retry these are the same number: every contact it makes
        # with an expired card IS a doomed charge retry.
        "dead_card_contacts": dead_card_attempts,
        "dead_card_attempts": dead_card_attempts,
        "refusals_honoured": 0,   # blind retry cannot hear a customer
    }


def agent_actuals() -> dict:
    """Pull the agent's real results for the same batch from the database."""
    init_db()
    conn = get_conn()
    try:
        recovered = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE recovery_status = 'recovered'").fetchone()
        attempts = conn.execute(
            "SELECT COUNT(*) FROM recovery_actions "
            "WHERE state IN ('succeeded','failed')").fetchone()[0]
        fraud_retries = conn.execute(
            "SELECT COUNT(*) FROM recovery_actions ra "
            "JOIN payments p ON p.id = ra.payment_id "
            "WHERE p.failure_code = 'FRAUD_SUSPECTED'").fetchone()[0]
        # Two DIFFERENT numbers, and conflating them would flatter the agent.
        # The agent does contact expired-card customers — it asks them for a
        # new card, which can actually work. What it never does is retry the
        # dead card itself, which cannot. Report both.
        dead_card_contacts = conn.execute(
            "SELECT COUNT(*) FROM recovery_actions ra "
            "JOIN payments p ON p.id = ra.payment_id "
            "WHERE p.failure_code = 'EXPIRED_CARD'").fetchone()[0]
        dead_card_attempts = conn.execute(
            "SELECT COUNT(*) FROM recovery_actions ra "
            "JOIN payments p ON p.id = ra.payment_id "
            "WHERE p.failure_code = 'EXPIRED_CARD' "
            "AND ra.action = 'retry'").fetchone()[0]
        refusals = conn.execute(
            "SELECT COUNT(*) FROM promises WHERE intent = 'refusal'"
        ).fetchone()[0]
        at_risk = conn.execute(
            "SELECT COALESCE(SUM(amount_paise),0) FROM payments "
            "WHERE status != 'captured'").fetchone()[0]
    finally:
        conn.close()
    return {
        "recovered_paise": recovered["amt"],
        "recovered_n": recovered["n"],
        "fraud_recovered_paise": 0,   # structurally impossible: hard rule
        "attempts": attempts,
        "fraud_retries": fraud_retries,
        "dead_card_contacts": dead_card_contacts,
        "dead_card_attempts": dead_card_attempts,
        "refusals_honoured": refusals,
        "at_risk_paise": at_risk,
    }


def compare() -> dict:
    return {"agent": agent_actuals(), "baseline": simulate_blind_retry()}


if __name__ == "__main__":
    c = compare()
    a, b = c["agent"], c["baseline"]
    rs = lambda p: f"Rs.{p / 100:,.0f}"
    at_risk = a["at_risk_paise"]
    pct = lambda p: f"{100 * p / at_risk:.0f}%" if at_risk else "-"
    print(f"{'':<38}{'Agent':>14}{'Blind retry x3':>16}")
    print(f"{'Recovered (clean)':<38}"
          f"{rs(a['recovered_paise']) + ' (' + pct(a['recovered_paise']) + ')':>14}"
          f"{rs(b['recovered_paise']) + ' (' + pct(b['recovered_paise']) + ')':>16}")
    print(f"{'\"Recovered\" from suspected fraud':<38}"
          f"{rs(a['fraud_recovered_paise']):>14}"
          f"{rs(b['fraud_recovered_paise']):>16}")
    print(f"{'Attempts made':<38}{a['attempts']:>14}{b['attempts']:>16}")
    print(f"{'Retries against suspected fraud':<38}"
          f"{a['fraud_retries']:>14}{b['fraud_retries']:>16}")
    print(f"{'Contacts about expired cards':<38}"
          f"{a['dead_card_contacts']:>14}{b['dead_card_contacts']:>16}")
    print(f"{'  ...of those, doomed charge retries':<38}"
          f"{a['dead_card_attempts']:>14}{b['dead_card_attempts']:>16}")
    print(f"{'Customer refusals honoured':<38}"
          f"{a['refusals_honoured']:>14}{b['refusals_honoured']:>16}")
