"""Payment batch simulator.

Creates a realistic batch of merchant payments:
  - Every payment gets a REAL Razorpay test-mode order (proves live API integration).
  - Payment OUTCOMES (success/failure) are simulated locally with a realistic
    failure distribution, because test mode has no customers to actually fail.
    This is disclosed openly — see README.

Failure taxonomy is modeled on real Razorpay/gateway error codes. The
FRAUD_SUSPECTED class exists specifically so the policy engine can demonstrate
a hard "never retry" rule.

Run:  python -m app.simulator --count 100
"""

import argparse
import random
import sys
import time

import razorpay

from app import config
from app.channels import assign
from app.db import get_conn, init_db, log_action, utcnow

FIRST_NAMES = ["Aarav", "Vivaan", "Diya", "Ananya", "Rohan", "Priya", "Kabir",
               "Ishaan", "Meera", "Sneha", "Arjun", "Nikhil", "Pooja", "Rahul",
               "Kavya", "Aditya", "Tanvi", "Varun", "Riya", "Sameer"]
LAST_NAMES = ["Sharma", "Patel", "Reddy", "Iyer", "Khan", "Gupta", "Nair",
              "Singh", "Das", "Mehta", "Joshi", "Kulkarni", "Bose", "Rao"]

# (status, failure_code, failure_message, weight)
OUTCOMES = [
    ("captured", None, None, 55),
    ("failed", "INSUFFICIENT_FUNDS",
     "Payment declined by the issuing bank due to insufficient funds.", 15),
    ("failed", "EXPIRED_CARD",
     "The card used for this payment has expired.", 5),
    ("failed", "GATEWAY_ERROR",
     "Payment could not be processed due to a temporary issue at the bank. "
     "Please try again after some time.", 8),
    ("failed", "UPI_TIMEOUT",
     "UPI payment request timed out before the customer could approve it.", 7),
    ("abandoned", "CHECKOUT_ABANDONED",
     "Customer opened checkout but did not complete the payment.", 8),
    ("failed", "FRAUD_SUSPECTED",
     "Payment declined by the risk engine. Do not honour.", 2),
]


def _pick_outcome(rng: random.Random):
    total = sum(w for *_, w in OUTCOMES)
    r = rng.uniform(0, total)
    upto = 0
    for status, code, msg, w in OUTCOMES:
        upto += w
        if r <= upto:
            return status, code, msg
    return OUTCOMES[0][:3]


def _make_customer(rng: random.Random):
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    email = f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@example.com"
    phone = f"+919{rng.randint(100000000, 999999999)}"
    return f"{first} {last}", email, phone


REQUEST_GAP_SECONDS = 0.25   # stay well under test-mode rate limits
MAX_RETRIES = 5


def _create_order_with_backoff(client, amount: int, receipt: str) -> str:
    """Create a Razorpay order, backing off exponentially on rate limits.

    Test mode rate-limits aggressive loops ("Too many requests"). We retry up
    to MAX_RETRIES with 2^n second waits before giving up.
    """
    for attempt in range(MAX_RETRIES):
        try:
            order = client.order.create({
                "amount": amount,
                "currency": "INR",
                "receipt": receipt,
                "notes": {"source": "reclaim-simulator"},
            })
            return order["id"]
        except razorpay.errors.BadRequestError as e:
            if "too many" not in str(e).lower() or attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** attempt
            print(f"  rate limited, waiting {wait}s "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})...")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def seed(count: int, seed_value: int | None, offline: bool) -> None:
    init_db()
    rng = random.Random(seed_value)

    client = None
    if not offline:
        if not config.razorpay_configured():
            print("ERROR: Razorpay test keys missing in .env "
                  "(or pass --offline to skip real order creation).")
            sys.exit(1)
        client = razorpay.Client(auth=(config.RAZORPAY_KEY_ID,
                                       config.RAZORPAY_KEY_SECRET))

    created = 0
    conn = get_conn()
    try:
        for i in range(1, count + 1):
            payment_id = f"pay_sim_{i:04d}"
            exists = conn.execute(
                "SELECT 1 FROM payments WHERE id = ?", (payment_id,)
            ).fetchone()
            if exists:
                continue  # idempotent re-runs: never duplicate a payment

            name, email, phone = _make_customer(rng)
            amount = rng.randint(200, 5000) * 100  # 200–5000 INR in paise
            status, code, msg = _pick_outcome(rng)

            rzp_order_id = None
            if client is not None:
                rzp_order_id = _create_order_with_backoff(
                    client, amount, payment_id)
                time.sleep(REQUEST_GAP_SECONDS)

            # Commit per payment, not per batch: once a real order exists on
            # Razorpay's side, our local record of it must survive any later
            # crash. A batch-wide transaction would roll back rows whose
            # external side effect already happened.
            with conn:
                method, bank = assign(payment_id, code)
                conn.execute(
                    "INSERT INTO payments (id, rzp_order_id, customer_name, "
                    "customer_email, customer_phone, amount_paise, status, "
                    "failure_code, failure_message, method, bank, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (payment_id, rzp_order_id, name, email, phone, amount,
                     status, code, msg, method, bank, utcnow()),
                )
                log_action(conn, payment_id, "simulator", "payment_seeded", {
                    "rzp_order_id": rzp_order_id,
                    "method": method,
                    "bank": bank,
                    "status": status,
                    "failure_code": code,
                    "amount_paise": amount,
                })
            created += 1
            if created % 10 == 0:
                print(f"  seeded {created}...")
    finally:
        conn.close()

    _print_summary(created)


def _print_summary(created: int) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) n, SUM(amount_paise) total "
            "FROM payments GROUP BY status"
        ).fetchall()
        risk = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) total "
            "FROM payments WHERE status != 'captured'"
        ).fetchone()
    print(f"\nSeeded {created} new payment(s). Batch state:")
    for r in rows:
        print(f"  {r['status']:<10} {r['n']:>4}  Rs.{r['total'] / 100:,.0f}")
    print(f"\nRevenue at risk: Rs.{risk['total'] / 100:,.0f} "
          f"across {risk['n']} payments")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed a synthetic payment batch")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed for reproducible batches")
    parser.add_argument("--offline", action="store_true",
                        help="Skip creating real Razorpay test orders")
    args = parser.parse_args()
    seed(args.count, args.seed, args.offline)
