"""Diagnoser: LLM-powered root-cause analysis of failed payments.

This is where AI earns its place: gateway failure messages are messy free text,
and drafting a customer-appropriate recovery message needs language judgment.

The LLM's output is ADVISORY. It never touches money. The policy engine applies
deterministic rules on top and can overrule any recommendation (e.g. a
FRAUD_SUSPECTED payment is never retried no matter what the model says).

Only the customer's first name is sent to the model — no email, phone, or ids.

Run:  python -m app.diagnoser [--limit 20]
"""

import argparse
import json
import time

from google import genai

from app import config
from app.db import get_conn, init_db, log_action, utcnow

MODEL = "gemini-2.5-flash"
REQUEST_GAP_SECONDS = 0.5

PROMPT_TEMPLATE = """\
You are a payments-recovery analyst for an Indian online merchant.
A payment has failed. Analyse it and reply with ONLY a JSON object, no prose.

Payment details:
- Amount: INR {amount:.0f}
- Customer first name: {first_name}
- Status: {status}
- Gateway message: "{failure_message}"

Reply with this exact JSON shape:
{{
  "root_cause": "<one short phrase, e.g. 'insufficient funds at issuing bank'>",
  "transient": <true if a later retry could plausibly succeed, else false>,
  "recommended_action": "<one of: retry | payment_link | update_card | escalate | none>",
  "customer_message": "<2-3 sentence friendly SMS/email draft asking the customer to complete their payment; include the amount; do not invent discounts or threaten>"
}}

Guidance:
- insufficient funds: transient (salaries land, balances change) -> payment_link
- expired card: not transient for the same card -> update_card
- bank/gateway outage or UPI timeout: transient, customer did nothing wrong -> retry
- abandoned checkout: customer hesitated -> payment_link with a gentle nudge
- anything suggesting fraud or risk decline: recommended_action must be "escalate"
"""


def diagnose_batch(limit: int) -> int:
    init_db()
    if not config.GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY missing in .env")
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    conn = get_conn()
    done = 0
    try:
        rows = conn.execute(
            "SELECT p.id, p.customer_name, p.amount_paise, p.status, "
            "       p.failure_message "
            "FROM payments p "
            "LEFT JOIN diagnoses d ON d.payment_id = p.id "
            "WHERE p.recovery_status = 'detected' AND d.payment_id IS NULL "
            "ORDER BY p.id LIMIT ?",
            (limit,),
        ).fetchall()

        for row in rows:
            prompt = PROMPT_TEMPLATE.format(
                amount=row["amount_paise"] / 100,
                first_name=row["customer_name"].split()[0],
                status=row["status"],
                failure_message=row["failure_message"] or "(none)",
            )
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            diagnosis = json.loads(response.text)

            required = {"root_cause", "transient", "recommended_action",
                        "customer_message"}
            missing = required - diagnosis.keys()
            if missing:
                raise ValueError(
                    f"{row['id']}: model reply missing fields {missing}")

            with conn:
                conn.execute(
                    "INSERT INTO diagnoses (payment_id, root_cause, transient, "
                    "recommended_action, customer_message, model, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["id"], diagnosis["root_cause"],
                     int(bool(diagnosis["transient"])),
                     diagnosis["recommended_action"],
                     diagnosis["customer_message"], MODEL, utcnow()),
                )
                log_action(conn, row["id"], "diagnoser", "diagnosed", diagnosis)
            done += 1
            print(f"  {row['id']}: {diagnosis['root_cause']} "
                  f"-> {diagnosis['recommended_action']}")
            time.sleep(REQUEST_GAP_SECONDS)
    finally:
        conn.close()
    return done


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20,
                        help="max payments to diagnose this run")
    args = parser.parse_args()
    n = diagnose_batch(args.limit)
    print(f"\nDiagnosed {n} payment(s).")
