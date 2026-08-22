"""SQLite persistence layer.

Two tables carry the whole system:
  payments  — one row per payment the merchant attempted to collect
  audit_log — append-only record of every action any component takes, and why.

The audit log is append-only by design: recovery decisions about money must be
reconstructable after the fact, including across process crashes.
"""

import json
import sqlite3
from datetime import datetime, timezone

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id              TEXT PRIMARY KEY,          -- internal id, e.g. pay_sim_0001
    rzp_order_id    TEXT,                      -- real Razorpay test-mode order id
    customer_name   TEXT NOT NULL,
    customer_email  TEXT NOT NULL,
    customer_phone  TEXT NOT NULL,
    amount_paise    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'INR',
    status          TEXT NOT NULL,             -- captured | failed | abandoned
    failure_code    TEXT,                      -- e.g. INSUFFICIENT_FUNDS (NULL if captured)
    failure_message TEXT,                      -- gateway-style human message
    recovery_status TEXT NOT NULL DEFAULT 'none',
        -- none | detected | in_progress | recovered | escalated | written_off
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnoses (
    payment_id        TEXT PRIMARY KEY,
    root_cause        TEXT NOT NULL,   -- LLM's classification of what went wrong
    transient         INTEGER NOT NULL,-- 1 if retrying could plausibly succeed
    recommended_action TEXT NOT NULL,  -- retry | payment_link | update_card | escalate | none
    customer_message  TEXT NOT NULL,   -- drafted outreach text (advisory only)
    model             TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (payment_id) REFERENCES payments(id)
);

CREATE TABLE IF NOT EXISTS recovery_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id      TEXT NOT NULL,
    action          TEXT NOT NULL,    -- retry | payment_link | update_card
    attempt         INTEGER NOT NULL DEFAULT 1,
    state           TEXT NOT NULL DEFAULT 'planned',
        -- planned | executing | succeeded | failed
    idempotency_key TEXT NOT NULL UNIQUE,
    rzp_link_id     TEXT,             -- real Razorpay payment-link id, if any
    rzp_link_url    TEXT,
    result          TEXT,             -- JSON outcome detail
    created_at      TEXT NOT NULL,
    executed_at     TEXT,
    FOREIGN KEY (payment_id) REFERENCES payments(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id  TEXT NOT NULL,
    actor       TEXT NOT NULL,   -- simulator | detector | diagnoser | policy | executor
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL,   -- JSON blob with the full reasoning/payload
    created_at  TEXT NOT NULL,
    FOREIGN KEY (payment_id) REFERENCES payments(id)
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def log_action(conn: sqlite3.Connection, payment_id: str, actor: str,
               action: str, detail: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log (payment_id, actor, action, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (payment_id, actor, action, json.dumps(detail), utcnow()),
    )
