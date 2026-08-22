"""Policy engine: turns the LLM's advice into bounded, auditable decisions.

This file is deliberately boring, deterministic Python. The design rule of the
whole system: THE LLM ADVISES, POLICY DECIDES. Money never moves on a model's
say-so alone.

Hard rules, in priority order:
  1. FRAUD_SUSPECTED is never recovered automatically — always escalated to a
     human, regardless of what the model recommended.
  2. Amounts above AMOUNT_CAP_PAISE always require human approval (escalate).
  3. The model's recommended action must be in the allowed set for that
     failure class; otherwise the policy default is used and the override is
     logged.
  4. At most MAX_ATTEMPTS recovery attempts per payment (enforced in the
     executor); exhaustion escalates, never loops.

Run:  python -m app.policy
"""

from app.db import get_conn, init_db, log_action, utcnow

MAX_ATTEMPTS = 3
AMOUNT_CAP_PAISE = 10_000 * 100  # above Rs.10,000 a human must approve

# failure class -> (allowed actions for the LLM to pick, policy default)
ACTION_POLICY = {
    "INSUFFICIENT_FUNDS": ({"payment_link", "retry"}, "payment_link"),
    "EXPIRED_CARD": ({"update_card"}, "update_card"),
    "GATEWAY_ERROR": ({"retry", "payment_link"}, "retry"),
    "UPI_TIMEOUT": ({"retry", "payment_link"}, "retry"),
    "CHECKOUT_ABANDONED": ({"payment_link"}, "payment_link"),
}


def plan() -> dict:
    init_db()
    stats = {"planned": 0, "escalated": 0, "llm_overridden": 0}
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT p.id, p.amount_paise, p.failure_code, "
            "       d.recommended_action, d.transient "
            "FROM payments p JOIN diagnoses d ON d.payment_id = p.id "
            "WHERE p.recovery_status = 'detected'"
        ).fetchall()

        for row in rows:
            pid = row["id"]
            code = row["failure_code"]

            # Rule 1: fraud is a hard stop. The LLM's opinion is irrelevant.
            if code == "FRAUD_SUSPECTED":
                _escalate(conn, pid,
                          "hard rule: risk-engine declines are never retried "
                          "automatically (overrides any LLM advice)")
                stats["escalated"] += 1
                continue

            # Rule 2: big amounts need a human.
            if row["amount_paise"] > AMOUNT_CAP_PAISE:
                _escalate(conn, pid,
                          f"hard rule: amount exceeds approval cap "
                          f"({AMOUNT_CAP_PAISE / 100:.0f} INR)")
                stats["escalated"] += 1
                continue

            allowed, default = ACTION_POLICY.get(code, (set(), None))
            if default is None:
                _escalate(conn, pid,
                          f"no recovery policy defined for failure class "
                          f"{code!r} — unknown territory goes to a human")
                stats["escalated"] += 1
                continue

            # Rule 3: LLM advice is taken only inside the allowed set.
            advised = row["recommended_action"]
            if advised in allowed:
                action = advised
                overridden = False
            else:
                action = default
                overridden = True
                stats["llm_overridden"] += 1

            key = f"{pid}:{action}:1"
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO recovery_actions "
                    "(payment_id, action, attempt, idempotency_key, created_at) "
                    "VALUES (?, ?, 1, ?, ?)",
                    (pid, action, key, utcnow()),
                )
                conn.execute(
                    "UPDATE payments SET recovery_status = 'in_progress' "
                    "WHERE id = ?", (pid,),
                )
                log_action(conn, pid, "policy", "action_planned", {
                    "action": action,
                    "llm_advised": advised,
                    "llm_overridden": overridden,
                    "allowed_for_class": sorted(allowed),
                    "attempt": 1,
                    "max_attempts": MAX_ATTEMPTS,
                    "idempotency_key": key,
                })
            stats["planned"] += 1
    finally:
        conn.close()
    return stats


def _escalate(conn, payment_id: str, reason: str) -> None:
    with conn:
        conn.execute(
            "UPDATE payments SET recovery_status = 'escalated' WHERE id = ?",
            (payment_id,),
        )
        log_action(conn, payment_id, "policy", "escalated_to_human",
                   {"reason": reason})


if __name__ == "__main__":
    s = plan()
    print(f"Planned {s['planned']} recovery action(s), "
          f"escalated {s['escalated']} to a human, "
          f"overrode LLM advice on {s['llm_overridden']}.")
