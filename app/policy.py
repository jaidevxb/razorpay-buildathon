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

from datetime import datetime, timedelta, timezone

from app.db import get_conn, init_db, log_action, utcnow

MAX_ATTEMPTS = 3
AMOUNT_CAP_PAISE = 10_000 * 100  # above Rs.10,000 a human must approve

# WHEN to act matters as much as what to do. Retrying an empty account at
# 2am fails again; a UPI re-request minutes later catches warm intent.
RETRY_DELAY_HOURS = {
    "GATEWAY_ERROR": 2,      # bank outages typically clear within hours
    "UPI_TIMEOUT": 0.25,     # re-request while purchase intent is warm
    "CHECKOUT_ABANDONED": 1, # gentle nudge, not an instant chase
}
LINK_RESEND_GAP_HOURS = 24   # between link reminders — more is spam
TIMING_RATIONALE = {
    "GATEWAY_ERROR": "bank outages typically clear within hours",
    "UPI_TIMEOUT": "re-request quickly while purchase intent is warm",
    "CHECKOUT_ABANDONED": "nudge after an hour, not instantly",
    "INSUFFICIENT_FUNDS": "deferred to the next salary window (1st of "
                          "month) — retrying an empty account tonight "
                          "fails again",
}


def schedule_for(action: str, failure_code: str, attempt: int) -> tuple:
    """Return (iso timestamp, rationale) for when an attempt should run.

    First contact (a payment link) goes out immediately; what gets timed is
    retries and reminders.
    """
    now = datetime.now(timezone.utc)
    if action == "retry" and failure_code == "INSUFFICIENT_FUNDS":
        # next 1st of the month, 10:00 IST — the salary window
        first = (now.replace(day=1) + timedelta(days=32)).replace(
            day=1, hour=4, minute=30, second=0, microsecond=0)
        return first.isoformat(), TIMING_RATIONALE["INSUFFICIENT_FUNDS"]
    if action == "retry":
        hours = RETRY_DELAY_HOURS.get(failure_code, 1) * attempt
        return ((now + timedelta(hours=hours)).isoformat(),
                TIMING_RATIONALE.get(failure_code, "spaced retry"))
    if attempt == 1:
        return now.isoformat(), "first contact goes out immediately"
    return ((now + timedelta(hours=LINK_RESEND_GAP_HOURS)).isoformat(),
            f"{LINK_RESEND_GAP_HOURS}h between reminders — more is spam")

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
            when, why_then = schedule_for(action, code, 1)
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO recovery_actions "
                    "(payment_id, action, attempt, idempotency_key, "
                    "scheduled_at, created_at) VALUES (?, ?, 1, ?, ?, ?)",
                    (pid, action, key, when, utcnow()),
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
                    "scheduled_at": when,
                    "timing": why_then,
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
