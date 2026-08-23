"""Get the batch to the point where recording should START.

The slow parts of the pipeline are the ones with nothing to watch: creating a
hundred Razorpay orders under a rate limit, and forty-five Gemini calls. Both
are throttled on purpose, both take minutes, and neither is interesting on
camera.

This runs exactly those steps and stops. It leaves the batch detected,
diagnosed and planned, with nothing recovered yet — so the first thing the
recording shows is a dashboard full of at-risk money and a zero next to
"won back", and the first thing that happens on camera is that zero moving.

Nothing here is skipped for the video's benefit; it is run for real, just
earlier. The executor, promises, red-team, baseline and tests all still run
live while recording.

Run:  python -m app.prep_demo            (about 5 minutes)
      python -m app.prep_demo --keep     (don't wipe; top up an existing batch)
"""

import argparse
import time

from app import detector, diagnoser, policy, reset, simulator
from app.db import get_conn, init_db


def _timed(label, fn, *args, **kwargs):
    print(f"\n>>> {label}")
    start = time.monotonic()
    result = fn(*args, **kwargs)
    print(f"    done in {time.monotonic() - start:.0f}s")
    return result


def main(wipe: bool, count: int, offline: bool) -> None:
    total = time.monotonic()

    if wipe:
        _timed("Clearing local state", reset.reset)

    _timed(f"Seeding {count} payments (real Razorpay test orders)",
           simulator.seed, count, 42, offline)
    _timed("Detecting revenue at risk", detector.detect)
    _timed("Diagnosing failures with Gemini", diagnoser.diagnose_batch, count)
    _timed("Planning bounded recovery actions", policy.plan)

    init_db()
    conn = get_conn()
    try:
        at_risk = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE status != 'captured'").fetchone()
        planned = conn.execute(
            "SELECT COUNT(*) FROM recovery_actions "
            "WHERE state = 'planned'").fetchone()[0]
        escalated = conn.execute(
            "SELECT COUNT(*) FROM payments "
            "WHERE recovery_status = 'escalated'").fetchone()[0]
        recovered = conn.execute(
            "SELECT COUNT(*) FROM payments "
            "WHERE recovery_status = 'recovered'").fetchone()[0]
    finally:
        conn.close()

    print(f"\n{'=' * 62}")
    print(f"Ready to record.  (setup took "
          f"{(time.monotonic() - total) / 60:.1f} min)")
    print(f"{'=' * 62}")
    print(f"  At risk        Rs.{at_risk['amt'] / 100:,.0f} "
          f"across {at_risk['n']} payments")
    print(f"  Actions queued {planned}")
    print(f"  Escalated      {escalated}  (fraud — refused before any action)")
    print(f"  Recovered      {recovered}  <- this is what moves on camera")
    print("\nStart the dashboard, then hit record:")
    print("  uvicorn app.main:app --port 8100")
    print("\nFirst command on camera (DEMO.md step 4):")
    print("  python -m app.executor --loop --pace 1 --force-due")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true",
                   help="don't wipe existing state first")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--offline", action="store_true",
                   help="skip real Razorpay orders (no network)")
    a = p.parse_args()
    main(wipe=not a.keep, count=a.count, offline=a.offline)
