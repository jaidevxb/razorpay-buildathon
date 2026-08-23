"""Reset the local database for a fresh demo run.

Deliberately requires --yes: this deletes all local batch state. It touches
NOTHING on Razorpay — test-mode orders/links there are left as-is.

Run:  python -m app.reset --yes
"""

import argparse

from app.config import DB_PATH


def reset() -> None:
    if not DB_PATH.exists():
        print("No database file — already clean.")
        return
    try:
        DB_PATH.unlink()
    except PermissionError:
        # Windows won't unlink a file another process holds open, and the
        # usual culprit is the dashboard. Say so plainly: a raw traceback
        # here is the last thing anyone needs mid-demo.
        raise SystemExit(
            f"Cannot delete {DB_PATH.name} — another process has it open.\n"
            f"The dashboard is almost certainly still running. Stop it "
            f"(Ctrl+C in its terminal) and run this again."
        ) from None
    print(f"Deleted {DB_PATH.name}. Next run starts from a clean batch.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true",
                        help="confirm deletion of all local batch state")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing without --yes (this wipes local state).")
    reset()
