"""Build the demo snapshot served by the public read-only dashboard.

Copies the local batch, then scrubs anything that should not be public:
Razorpay object ids and synthetic contact details. The recovery decisions,
audit trail and metrics are left exactly as they ran — the demo shows a real
finished batch, not a mock-up.

Run:  python -m app.make_demo_db
"""

import shutil
import sqlite3
from pathlib import Path

from app.config import DB_PATH

DEMO_PATH = Path(__file__).resolve().parent.parent / "demo" / "reclaim-demo.db"


def build() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"no local batch at {DB_PATH} — run the pipeline first")
    DEMO_PATH.parent.mkdir(exist_ok=True)
    shutil.copy(DB_PATH, DEMO_PATH)

    conn = sqlite3.connect(DEMO_PATH)
    with conn:
        # Razorpay ids identify a real (test-mode) account; contact details are
        # synthetic but there is no reason to publish them either.
        conn.execute("UPDATE payments SET rzp_order_id = NULL, "
                     "customer_email = '', customer_phone = ''")
        conn.execute("UPDATE recovery_actions SET rzp_link_id = NULL, "
                     "rzp_link_url = NULL")
        conn.execute(
            "UPDATE audit_log SET detail = json_remove(detail, "
            "'$.rzp_order_id', '$.rzp_link_id', '$.rzp_link_url') "
            "WHERE json_valid(detail)")
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("payments", "diagnoses", "recovery_actions", "promises",
                  "audit_log")
    }
    conn.execute("VACUUM")
    conn.close()

    size_kb = DEMO_PATH.stat().st_size / 1024
    print(f"Wrote {DEMO_PATH.relative_to(DEMO_PATH.parent.parent)} "
          f"({size_kb:.0f} KB)")
    for table, n in counts.items():
        print(f"  {table:<18}{n:>6}")


if __name__ == "__main__":
    build()
