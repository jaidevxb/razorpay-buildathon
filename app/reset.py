"""Reset the local database for a fresh demo run.

Deliberately requires --yes: this deletes all local batch state. It touches
NOTHING on Razorpay — test-mode orders/links there are left as-is.

Run:  python -m app.reset --yes
"""

import argparse

from app.config import DB_PATH


def reset() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Deleted {DB_PATH.name}. Next run starts from a clean batch.")
    else:
        print("No database file — already clean.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true",
                        help="confirm deletion of all local batch state")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing without --yes (this wipes local state).")
    reset()
