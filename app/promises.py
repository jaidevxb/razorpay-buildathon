"""Promise-to-pay tracker.

When a customer replies to a payment link ("salary Friday ko aayegi, will pay
then"), blindly retrying is both annoying and pointless. This module:

  1. PARSES the reply with Gemini — replies are Hinglish free text, exactly
     the kind of input deterministic code handles badly and an LLM handles
     well. Output is structured: intent + promised timeframe.
  2. DECIDES with deterministic policy (never the LLM):
       - promise within 7 days  -> wait for the promised date
       - promise beyond 7 days  -> escalate (too far out to auto-track)
       - refusal                -> write off and STOP CONTACTING (a "no" is
                                   honoured; compliance over recovery rate)
       - claims already paid    -> escalate (a human must verify, the agent
                                   must never argue with a customer)
  3. RESOLVES due promises: kept -> recovered; broken -> escalate. One
     promise per payment — no promise-extension loops.

Run:  python -m app.promises --parse            (classify new replies)
      python -m app.promises --resolve          (settle promises now due)
      python -m app.promises --resolve --force  (demo: treat all as due)
"""

import argparse
import json
import random
from datetime import datetime, timedelta, timezone

from google import genai

from app import config
from app.db import get_conn, init_db, log_action, utcnow

MODEL = "gemini-2.5-flash"
MAX_PROMISE_DAYS = 7
KEPT_PROBABILITY = 0.6   # simulated: fraction of promises actually honoured

PARSE_PROMPT = """\
A customer of an Indian online merchant was sent a payment link for a failed
payment and replied. Replies are often Hinglish. Classify the reply.

Reply: "{reply}"
Today is {today}.

Answer with ONLY this JSON:
{{
  "intent": "<promise_to_pay | refusal | claims_paid | unclear>",
  "days_until_pay": <integer days from today they promise to pay, or null>
}}"""


def parse_new_replies() -> int:
    """Classify replies in 'received' state and apply promise policy."""
    init_db()
    if not config.GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY missing in .env")
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    conn = get_conn()
    handled = 0
    try:
        rows = conn.execute(
            "SELECT * FROM promises WHERE status = 'received'").fetchall()
        for row in rows:
            resp = client.models.generate_content(
                model=MODEL,
                contents=PARSE_PROMPT.format(
                    reply=row["raw_reply"],
                    today=datetime.now(timezone.utc).date().isoformat()),
                config={"response_mime_type": "application/json"},
            )
            parsed = json.loads(resp.text)
            intent = parsed.get("intent", "unclear")
            days = parsed.get("days_until_pay")

            pid = row["payment_id"]
            with conn:
                log_action(conn, pid, "promise-tracker", "reply_parsed", {
                    "raw_reply": row["raw_reply"],
                    "intent": intent,
                    "days_until_pay": days,
                    "model": MODEL,
                })
                if intent == "promise_to_pay" and days is not None \
                        and 0 <= days <= MAX_PROMISE_DAYS:
                    due = (datetime.now(timezone.utc)
                           + timedelta(days=days)).isoformat()
                    conn.execute(
                        "UPDATE promises SET intent = ?, due_at = ?, "
                        "status = 'pending' WHERE id = ?",
                        (intent, due, row["id"]),
                    )
                    log_action(conn, pid, "promise-tracker",
                               "promise_registered",
                               {"due_at": due,
                                "policy": f"promises up to {MAX_PROMISE_DAYS} "
                                          "days are tracked; retries pause "
                                          "until the promised date"})
                elif intent == "promise_to_pay":
                    _close(conn, row["id"], intent, "escalated", pid,
                           f"promise beyond {MAX_PROMISE_DAYS} days — too "
                           "far out to auto-track, a human decides")
                elif intent == "refusal":
                    _close(conn, row["id"], intent, "written_off", pid,
                           "customer refused — contact stops immediately; "
                           "a 'no' is honoured over recovery rate")
                elif intent == "claims_paid":
                    _close(conn, row["id"], intent, "escalated", pid,
                           "customer claims they already paid — a human "
                           "verifies; the agent never argues with customers")
                else:
                    _close(conn, row["id"], intent, "escalated", pid,
                           "reply not understood — unclear cases go to a "
                           "human, not into a guessing loop")
            handled += 1
            print(f"  {pid}: {intent}"
                  + (f" (in {days}d)" if days is not None else ""))
    finally:
        conn.close()
    return handled


def _close(conn, promise_id: int, intent: str, payment_state: str,
           pid: str, reason: str) -> None:
    conn.execute(
        "UPDATE promises SET intent = ?, status = 'closed', resolved_at = ? "
        "WHERE id = ?", (intent, utcnow(), promise_id),
    )
    conn.execute(
        "UPDATE payments SET recovery_status = ? WHERE id = ?",
        (payment_state, pid),
    )
    log_action(conn, pid, "promise-tracker",
               f"payment_{payment_state}", {"reason": reason})


def resolve_due(force: bool = False) -> int:
    """Settle pending promises whose due date has arrived."""
    init_db()
    conn = get_conn()
    handled = 0
    try:
        now = utcnow()
        if force:
            rows = conn.execute(
                "SELECT * FROM promises WHERE status = 'pending'").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM promises WHERE status = 'pending' "
                "AND due_at <= ?", (now,)).fetchall()
        for row in rows:
            pid = row["payment_id"]
            # Simulated: did the customer keep the promise? Seeded per promise
            # so re-runs agree with themselves.
            kept = random.Random(f"promise:{row['id']}").random() \
                < KEPT_PROBABILITY
            with conn:
                if kept:
                    conn.execute(
                        "UPDATE promises SET status = 'kept', "
                        "resolved_at = ? WHERE id = ?", (now, row["id"]),
                    )
                    conn.execute(
                        "UPDATE payments SET recovery_status = 'recovered' "
                        "WHERE id = ?", (pid,),
                    )
                    log_action(conn, pid, "promise-tracker", "promise_kept", {
                        "simulated_outcome": True,
                        "note": "customer paid within the promised window",
                    })
                else:
                    conn.execute(
                        "UPDATE promises SET status = 'broken', "
                        "resolved_at = ? WHERE id = ?", (now, row["id"]),
                    )
                    conn.execute(
                        "UPDATE payments SET recovery_status = 'escalated' "
                        "WHERE id = ?", (pid,),
                    )
                    log_action(conn, pid, "promise-tracker",
                               "promise_broken", {
                                   "simulated_outcome": True,
                                   "policy": "one promise per payment; a "
                                             "broken promise goes to a human, "
                                             "not another automated cycle",
                               })
            handled += 1
            print(f"  {pid}: {'kept' if kept else 'broken'}")
    finally:
        conn.close()
    return handled


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--parse", action="store_true")
    parser.add_argument("--resolve", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="with --resolve: treat every pending promise "
                             "as due now (demo pacing)")
    args = parser.parse_args()
    if args.parse:
        n = parse_new_replies()
        print(f"Parsed {n} customer repl(ies).")
    if args.resolve:
        n = resolve_due(force=args.force)
        print(f"Resolved {n} promise(s).")
    if not (args.parse or args.resolve):
        print("nothing to do: pass --parse and/or --resolve")
