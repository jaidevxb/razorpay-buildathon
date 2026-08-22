"""Executor: performs planned recovery actions, safely.

Safety model:
  - Every action has a unique idempotency key. Before any external call the
    action row is marked 'executing' and committed — so after a crash we KNOW
    which actions were in flight.
  - On startup, in-flight actions are reconciled against Razorpay by reference
    id: if the payment link already exists out there, we adopt it instead of
    creating a duplicate. This is what makes kill-and-restart safe.
  - payment_link / update_card actions create REAL Razorpay test-mode payment
    links. Whether the (synthetic) customer then pays is simulated with a
    deterministic per-payment RNG, because test mode has no humans in it.
    Retry outcomes are simulated the same way. Disclosed openly in the README.
  - A failed attempt schedules the next one only up to policy.MAX_ATTEMPTS,
    then escalates. In production the cooldown between attempts would be hours
    or days; here it is one executor pass, so a demo fits in minutes.

Run:  python -m app.executor           (one pass)
      python -m app.executor --loop    (passes until nothing is planned)
"""

import argparse
import json
import random
import time

import razorpay

from app import config
from app.db import get_conn, init_db, log_action, utcnow
from app.policy import MAX_ATTEMPTS

REQUEST_GAP_SECONDS = 2.0   # payment-link API is rate-limited harder than orders
MAX_API_RETRIES = 8


def _with_backoff(fn, *args):
    """Call a Razorpay SDK function, backing off exponentially on rate limits."""
    for attempt in range(MAX_API_RETRIES):
        try:
            return fn(*args)
        except razorpay.errors.BadRequestError as e:
            if "too many" not in str(e).lower() or attempt == MAX_API_RETRIES - 1:
                raise
            wait = min(60, 2 ** attempt)
            print(f"  rate limited, waiting {wait}s "
                  f"(attempt {attempt + 1}/{MAX_API_RETRIES})...")
            time.sleep(wait)

# Probability the simulated customer/bank cooperates, per action+failure class.
SIM_SUCCESS_PROB = {
    ("retry", "GATEWAY_ERROR"): 0.70,
    ("retry", "UPI_TIMEOUT"): 0.60,
    ("retry", "INSUFFICIENT_FUNDS"): 0.30,
    ("payment_link", "INSUFFICIENT_FUNDS"): 0.55,
    ("payment_link", "CHECKOUT_ABANDONED"): 0.50,
    ("payment_link", "GATEWAY_ERROR"): 0.60,
    ("payment_link", "UPI_TIMEOUT"): 0.60,
    ("update_card", "EXPIRED_CARD"): 0.45,
}


def _client():
    if not config.razorpay_configured():
        raise SystemExit("Razorpay test keys missing in .env")
    return razorpay.Client(auth=(config.RAZORPAY_KEY_ID,
                                 config.RAZORPAY_KEY_SECRET))


def _create_payment_link(client, payment, action_row, check_existing=False):
    """Create a real Razorpay payment link, idempotently via reference_id.

    check_existing is only set on the crash-reconciliation path: a fresh
    'planned' action cannot have a pre-existing link, so skipping the lookup
    halves our API calls against the tightly rate-limited link endpoint.
    """
    key = action_row["idempotency_key"]
    if check_existing:
        existing = _with_backoff(client.payment_link.all,
                                 {"reference_id": key})
        if existing.get("items"):
            link = existing["items"][0]
            return link["id"], link["short_url"], True

    purpose = ("card update and payment"
               if action_row["action"] == "update_card" else "payment")
    try:
        link = _with_backoff(client.payment_link.create, {
            "amount": payment["amount_paise"],
            "currency": "INR",
            "reference_id": key,
            "description": f"Complete your {purpose} of "
                           f"INR {payment['amount_paise'] / 100:.0f}",
            "customer": {
                "name": payment["customer_name"],
                "email": payment["customer_email"],
                "contact": payment["customer_phone"],
            },
            "notify": {"sms": False, "email": False},
        })
    except razorpay.errors.ServerError as e:
        if "limit of 30" not in str(e):
            raise
        # Test mode caps payment links at 30 EVER — cancelling does not free
        # the quota, and only live-mode KYC lifts it. Degrade gracefully:
        # continue the recovery with a simulated link rather than halting the
        # whole batch, and mark the degradation clearly in the result.
        return None, None, False
    return link["id"], link["short_url"], False


def _simulated_outcome(action_row, payment) -> bool:
    """Deterministic pseudo-random customer/bank response.

    Seeded by the idempotency key so a re-run after a crash reaches the same
    conclusion — the demo is reproducible and no attempt is double-counted.
    """
    prob = SIM_SUCCESS_PROB.get(
        (action_row["action"], payment["failure_code"]), 0.5)
    rng = random.Random(action_row["idempotency_key"])
    return rng.random() < prob


def reconcile_in_flight(conn, client) -> int:
    """Recover from a crash: resolve every action stuck in 'executing'."""
    stuck = conn.execute(
        "SELECT ra.*, p.amount_paise, p.customer_name, p.customer_email, "
        "       p.customer_phone, p.failure_code "
        "FROM recovery_actions ra JOIN payments p ON p.id = ra.payment_id "
        "WHERE ra.state = 'executing'"
    ).fetchall()
    for row in stuck:
        log_action(conn, row["payment_id"], "executor",
                   "reconciling_in_flight_action",
                   {"idempotency_key": row["idempotency_key"],
                    "note": "process died mid-action; resolving via "
                            "Razorpay reference lookup"})
        _finish_action(conn, client, row, reconciling=True)
    return len(stuck)


def _finish_action(conn, client, row, reconciling=False) -> None:
    """Complete one action from the 'executing' state onward."""
    pid = row["payment_id"]
    link_id = link_url = None
    link_degraded = False
    if row["action"] in ("payment_link", "update_card"):
        link_id, link_url, adopted = _create_payment_link(
            client, row, row, check_existing=reconciling)
        if adopted:
            log_action(conn, pid, "executor", "adopted_existing_link",
                       {"rzp_link_id": link_id,
                        "idempotency_key": row["idempotency_key"]})
        if link_id is None:
            link_degraded = True
            log_action(conn, pid, "executor", "link_quota_fallback", {
                "reason": "test-mode cap of 30 payment links reached; "
                          "continuing with simulated link delivery instead "
                          "of halting the batch",
            })
        time.sleep(REQUEST_GAP_SECONDS)

    success = _simulated_outcome(row, row)
    result = {
        "success": success,
        "simulated_outcome": True,
        "rzp_link_id": link_id,
        "link_simulated_due_to_quota": link_degraded,
    }

    # Cancel the link once its attempt has resolved. Two reasons:
    #  1. Correctness: a live link for a resolved/superseded attempt is a
    #     double-payment risk (customer pays a stale link after we already
    #     retried or escalated).
    #  2. Practicality: test mode caps you at 30 links total; cancelled links
    #     free the quota.
    if link_id is not None:
        try:
            _with_backoff(client.payment_link.cancel, link_id)
            log_action(conn, pid, "executor", "link_cancelled",
                       {"rzp_link_id": link_id,
                        "reason": "attempt resolved; stale links are a "
                                  "double-payment risk"})
        except razorpay.errors.BadRequestError as e:
            # e.g. already paid/cancelled — safe to leave, just record it
            log_action(conn, pid, "executor", "link_cancel_skipped",
                       {"rzp_link_id": link_id, "error": str(e)})

    with conn:
        conn.execute(
            "UPDATE recovery_actions SET state = ?, rzp_link_id = ?, "
            "rzp_link_url = ?, result = ?, executed_at = ? WHERE id = ?",
            ("succeeded" if success else "failed", link_id, link_url,
             json.dumps(result), utcnow(), row["id"]),
        )
        if success:
            conn.execute(
                "UPDATE payments SET recovery_status = 'recovered', "
                "retry_count = ? WHERE id = ?", (row["attempt"], pid),
            )
            log_action(conn, pid, "executor", "recovered", {
                "action": row["action"],
                "attempt": row["attempt"],
                "amount_paise": row["amount_paise"],
                "rzp_link_url": link_url,
            })
        else:
            log_action(conn, pid, "executor", "attempt_failed", {
                "action": row["action"],
                "attempt": row["attempt"],
                "rzp_link_url": link_url,
            })
            if row["attempt"] >= MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE payments SET recovery_status = 'escalated', "
                    "retry_count = ? WHERE id = ?", (row["attempt"], pid),
                )
                log_action(conn, pid, "executor", "escalated_to_human", {
                    "reason": f"stopping rule: {MAX_ATTEMPTS} attempts "
                              "exhausted — a human takes over, the agent "
                              "does not loop forever",
                })
            else:
                nxt = row["attempt"] + 1
                key = f"{pid}:{row['action']}:{nxt}"
                conn.execute(
                    "INSERT OR IGNORE INTO recovery_actions "
                    "(payment_id, action, attempt, idempotency_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (pid, row["action"], nxt, key, utcnow()),
                )
                conn.execute(
                    "UPDATE payments SET retry_count = ? WHERE id = ?",
                    (row["attempt"], pid),
                )
                log_action(conn, pid, "executor", "next_attempt_planned", {
                    "attempt": nxt,
                    "cooldown_note": "production would wait hours/days here",
                })


def run_pass(client=None, pace_seconds: float = 0.0) -> int:
    """Execute every currently-planned action once. Returns actions handled."""
    init_db()
    client = client or _client()
    conn = get_conn()
    try:
        handled = reconcile_in_flight(conn, client)

        rows = conn.execute(
            "SELECT ra.*, p.amount_paise, p.customer_name, p.customer_email, "
            "       p.customer_phone, p.failure_code "
            "FROM recovery_actions ra JOIN payments p ON p.id = ra.payment_id "
            "WHERE ra.state = 'planned' ORDER BY ra.id"
        ).fetchall()
        for row in rows:
            # Mark in-flight BEFORE the external call and commit, so a crash
            # here leaves evidence for reconciliation.
            with conn:
                conn.execute(
                    "UPDATE recovery_actions SET state = 'executing' "
                    "WHERE id = ?", (row["id"],),
                )
                log_action(conn, row["payment_id"], "executor",
                           "executing_action",
                           {"action": row["action"],
                            "attempt": row["attempt"],
                            "idempotency_key": row["idempotency_key"]})
            _finish_action(conn, client, row)
            handled += 1
            print(f"  {row['payment_id']} attempt {row['attempt']} "
                  f"({row['action']})")
            if pace_seconds:
                time.sleep(pace_seconds)
    finally:
        conn.close()
    return handled


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true",
                        help="run passes until no planned actions remain")
    parser.add_argument("--pace", type=float, default=0.0,
                        help="seconds to pause between actions (demo pacing)")
    args = parser.parse_args()

    client = _client()
    total = 0
    while True:
        n = run_pass(client, args.pace)
        total += n
        if not args.loop or n == 0:
            break
    print(f"\nHandled {total} action(s).")
