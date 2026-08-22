"""Central configuration. All secrets come from .env — nothing is hardcoded."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DB_PATH = PROJECT_ROOT / "reclaim.db"


def razorpay_configured() -> bool:
    return RAZORPAY_KEY_ID.startswith("rzp_test_") and bool(RAZORPAY_KEY_SECRET)
