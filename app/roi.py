"""ROI: what the agent itself costs per rupee it recovers.

An agent that moves money should know it isn't free. Costs counted:

  - Gemini calls (diagnosis + reply parsing), priced per call from published
    per-token rates and measured typical prompt sizes for this workload
    (~350 input / ~150 output tokens per call).
  - Customer outreach (payment links imply an SMS + email notification in
    production; notional Rs.0.20 per contact).
  - Razorpay API calls themselves carry no per-call fee (MDR applies to the
    recovered payment either way and is excluded — the merchant pays it on
    any successful payment, agent or not).

All assumptions are constants below, deliberately visible.

Run:  python -m app.roi
"""

from app.db import get_conn, init_db

# gemini-2.5-flash list pricing (USD per 1M tokens), converted at Rs.90/USD
INPUT_COST_PER_MTOK_USD = 0.30
OUTPUT_COST_PER_MTOK_USD = 2.50
USD_INR = 90.0
TOKENS_IN_PER_CALL = 350
TOKENS_OUT_PER_CALL = 150
OUTREACH_COST_PAISE = 20     # notional SMS+email per link sent


def gemini_cost_paise_per_call() -> float:
    usd = (TOKENS_IN_PER_CALL / 1e6 * INPUT_COST_PER_MTOK_USD
           + TOKENS_OUT_PER_CALL / 1e6 * OUTPUT_COST_PER_MTOK_USD)
    return usd * USD_INR * 100


def compute() -> dict:
    init_db()
    conn = get_conn()
    try:
        llm_calls = conn.execute(
            "SELECT (SELECT COUNT(*) FROM diagnoses) + "
            "(SELECT COUNT(*) FROM promises WHERE intent IS NOT NULL)"
        ).fetchone()[0]
        outreach = conn.execute(
            "SELECT COUNT(*) FROM recovery_actions "
            "WHERE action IN ('payment_link', 'update_card') "
            "AND state IN ('succeeded', 'failed')").fetchone()[0]
        recovered = conn.execute(
            "SELECT COALESCE(SUM(amount_paise),0) FROM payments "
            "WHERE recovery_status = 'recovered'").fetchone()[0]
    finally:
        conn.close()

    llm_cost = llm_calls * gemini_cost_paise_per_call()
    outreach_cost = outreach * OUTREACH_COST_PAISE
    total = llm_cost + outreach_cost
    # paise-per-paise ratio scaled to "rupees spent per Rs.100 recovered"
    per_100_rupees = (total / recovered * 100) if recovered else 0
    return {
        "llm_calls": llm_calls,
        "llm_cost_paise": llm_cost,
        "outreach_count": outreach,
        "outreach_cost_paise": outreach_cost,
        "total_cost_paise": total,
        "recovered_paise": recovered,
        "cost_per_100_recovered_rupees": per_100_rupees,
    }


if __name__ == "__main__":
    r = compute()
    print(f"LLM calls: {r['llm_calls']} "
          f"(Rs.{r['llm_cost_paise'] / 100:.2f})")
    print(f"Outreach sent: {r['outreach_count']} "
          f"(Rs.{r['outreach_cost_paise'] / 100:.2f})")
    print(f"Total agent cost: Rs.{r['total_cost_paise'] / 100:.2f}")
    print(f"Recovered: Rs.{r['recovered_paise'] / 100:,.0f}")
    print(f"=> Rs.{r['cost_per_100_recovered_rupees']:.3f} spent "
          f"per Rs.100 recovered")
