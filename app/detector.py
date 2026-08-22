"""Detector: finds revenue at risk.

Deliberately NOT an LLM. Deciding *whether* money is at risk is a set-membership
question the database answers exactly; an LLM here would add cost and
non-determinism for zero benefit. AI enters one step later, at diagnosis, where
there is genuine ambiguity to resolve.

Run:  python -m app.detector
"""

from app.db import get_conn, init_db, log_action

DETECTION_RULES = {
    "failed": "payment attempt failed at the gateway",
    "abandoned": "checkout opened but never completed",
}


def detect() -> int:
    init_db()
    flagged = 0
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, status, amount_paise, failure_code FROM payments "
            "WHERE status != 'captured' AND recovery_status = 'none'"
        ).fetchall()
        for row in rows:
            reason = DETECTION_RULES.get(row["status"], "unknown risk state")
            with conn:
                conn.execute(
                    "UPDATE payments SET recovery_status = 'detected' "
                    "WHERE id = ?", (row["id"],),
                )
                log_action(conn, row["id"], "detector", "flagged_at_risk", {
                    "rule": row["status"],
                    "reason": reason,
                    "failure_code": row["failure_code"],
                    "amount_paise": row["amount_paise"],
                })
            flagged += 1
    finally:
        conn.close()
    return flagged


if __name__ == "__main__":
    n = detect()
    print(f"Flagged {n} payment(s) as at-risk revenue.")
