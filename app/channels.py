"""Payment method and bank assignment.

Two reasons this exists as its own module rather than inline in the simulator:

1. A merchant's blended success rate hides the problem. 94% on UPI and 78% on
   cards averages to a healthy-looking 90%, and the card problem is invisible.
   Recovery decisions and the dashboard both need the channel dimension.

2. Assignment is driven by an INDEPENDENT random stream seeded per payment id,
   not by the simulator's shared stream. That matters: adding a field this way
   does not shift any other draw, so re-seeding a batch reproduces exactly the
   same failures and outcomes as before, plus the new field. A demo's numbers
   should not move because a column was added.
"""

import random

METHODS = ["upi", "card", "netbanking", "wallet"]

# Which methods can plausibly produce which failure, with weights.
METHOD_WEIGHTS = {
    "UPI_TIMEOUT":        {"upi": 1.0},
    "EXPIRED_CARD":       {"card": 1.0},
    "FRAUD_SUSPECTED":    {"card": 0.8, "netbanking": 0.2},
    "INSUFFICIENT_FUNDS": {"upi": 0.45, "card": 0.35, "netbanking": 0.20},
    "GATEWAY_ERROR":      {"netbanking": 0.4, "card": 0.35, "upi": 0.25},
    "CHECKOUT_ABANDONED": {"upi": 0.5, "card": 0.3, "wallet": 0.2},
    None:                 {"upi": 0.5, "card": 0.3, "netbanking": 0.15,
                           "wallet": 0.05},
}

BANKS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "PNB", "Yes Bank",
         "IndusInd"]
WALLETS = ["Paytm", "PhonePe", "Amazon Pay"]

# Deliberately uneven: one bank carries more traffic AND more trouble, so the
# per-channel view has something real to surface.
BANK_WEIGHTS = [0.24, 0.18, 0.16, 0.12, 0.10, 0.08, 0.07, 0.05]


def _weighted(rng, mapping):
    total = sum(mapping.values())
    r = rng.uniform(0, total)
    upto = 0.0
    for key, weight in mapping.items():
        upto += weight
        if r <= upto:
            return key
    return next(iter(mapping))


def assign(payment_id: str, failure_code: str | None) -> tuple[str, str]:
    """Return (method, bank) for a payment. Stable for a given payment id."""
    rng = random.Random(f"channel:{payment_id}")
    method = _weighted(rng, METHOD_WEIGHTS.get(failure_code,
                                               METHOD_WEIGHTS[None]))
    if method == "wallet":
        return method, rng.choice(WALLETS)
    bank = _weighted(rng, dict(zip(BANKS, BANK_WEIGHTS)))
    return method, bank
