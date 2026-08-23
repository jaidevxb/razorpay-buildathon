# Demo runbook

The full pipeline, start to finish, with the two showpiece moments marked.
Every command runs from the project root with the venv active
(`.\.venv\Scripts\activate`, or prefix commands with `.\.venv\Scripts\`).

## 0. Start clean

```
python -m app.reset --yes
uvicorn app.main:app --port 8100        # dashboard: http://127.0.0.1:8100
```

## 1. Seed the batch (real Razorpay test-mode orders)

```
python -m app.simulator --count 100
```

~100 orders appear in the Razorpay test dashboard. Locally: 100 payments,
roughly 45 of them failed/abandoned — the at-risk revenue.

## 2. Detect and diagnose

```
python -m app.detector
python -m app.diagnoser --limit 50
```

Detection is plain SQL (no AI needed for a yes/no question). Diagnosis is
Gemini: root cause + recommended action + drafted customer message per
failure, visible on each payment's detail page.

## 3. Plan recoveries (policy engine)

```
python -m app.policy
```

Watch the dashboard: fraud-suspected payments jump straight to *escalated* —
hard rule, the LLM's advice is never consulted for money safety.

## 4. Execute — SHOWPIECE 1: kill and resume

```
python -m app.executor --loop --pace 1
```

Mid-run, hit **Ctrl+C**. The dashboard freezes with actions stuck in
`executing`. Then:

```
python -m app.executor --loop --pace 1
```

The first thing the restarted executor does is reconcile in-flight actions
against Razorpay by idempotency reference — nothing is double-executed,
nothing is lost. The audit trail of the affected payment shows the
`reconciling_in_flight_action` entry.

## 5. Promises — SHOWPIECE 2: Hinglish replies

Some customers replied to their payment links instead of paying
("salary aane do, Friday ko pakka kar dunga"). Parse and settle them:

```
python -m app.promises --parse
python -m app.promises --resolve --force    # --force: demo pacing, treat due now
```

Gemini parses the Hinglish; deterministic policy decides: short promises are
tracked, refusals stop contact immediately (written off), "already paid"
claims and unclear replies go to a human.

## 6. Read the result

Dashboard shows the final split: recovered / escalated / written off, the
recovery rate per failure class, and every payment's full audit trail.

## Honest-simulation disclosure

Orders and payment links are real Razorpay test-mode API objects. Payment
*outcomes* (does the simulated customer pay / keep a promise) are simulated
with seeded RNG, because test mode has no humans. The Razorpay account's
lifetime cap of 30 test-mode payment links is exhausted, so link creation
degrades gracefully to simulated delivery — disclosed per-action in the
audit trail (`link_quota_fallback`).
