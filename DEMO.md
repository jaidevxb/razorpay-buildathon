# Demo runbook

The full pipeline, start to finish, with the three showpiece moments marked.
If you are recording, read step 0 first — the dashboard should already be open
in a second window before you start talking.
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
python -m app.executor --loop --pace 1 --force-due
```

(`--force-due` collapses the timing policy for demo pacing — in production an
insufficient-funds retry waits for the salary window, an outage retry waits
hours, link reminders sit 24h apart. Run once *without* the flag first to show
actions being deliberately held back, with the rationale in the audit log.)

Mid-run, hit **Ctrl+C**. The dashboard freezes with actions stuck in
`executing`. Then:

```
python -m app.executor --loop --pace 1 --force-due
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
tracked, small refusals stop contact immediately (written off), larger
refusals pause for a human to confirm before money is given up, and "already
paid" or unclear replies go to a human.

## 6. SHOWPIECE 3: attack your own agent

A customer reply is attacker-controlled text. Send the agent a hostile one:

```
python -m app.redteam --list        # six real payloads
python -m app.redteam --attack 1    # instruction override -> forgive the debt
python -m app.promises --parse
```

Output: `QUARANTINED — tries to override earlier instructions`. The reply is
screened before any model call, the payment is held for human review, and no
money moves. On the dashboard it appears in Customer promises as **blocked**
with the reason.

Say the line: *"This payload used to work. It made the agent write off the
debt. I found it by attacking my own system."*

## 7. The human's seat — escalation queue

Open **/escalations**: every case the agent deliberately stopped on, with the
reason. Click "Collected manually" or "Write off" — the decision lands in the
audit trail as `actor: human`.

## 8. The honest comparison

```
python -m app.baseline
```

Same batch through blind auto-retry ×3 (what most merchants do): about half
the recovery, 60% more attempts, retries against suspected fraud, 18 attempts
on dead cards, and it can't hear a customer say no. Also rendered as the
comparison table on the dashboard.

## 9. Where the money actually leaks

```
python -m app.health
```

Two things a blended success rate hides, both also on the dashboard in
"Where you're losing money" and "Bank trouble":

- **Per method.** UPI is 60% paid on the first try, cards only 43% — and
  cards are where the agent recovers most (43% → 86%). Averaged together
  that's an unremarkable-looking number and the card problem is invisible.
- **Per bank.** SBI is failing 67% of recovery attempts against a 45% batch
  norm, so its payments are **held rather than attempted — and the attempt is
  not consumed.** Attempts are a budget of three; spending one while a bank is
  broken wastes it.

Say the line: *"The threshold is relative to the batch norm, not a fixed
number. A fixed one flags every bank on a bad day and misses a broken one on
a good day."*

## 10. What the agent itself costs

```
python -m app.roi
```

LLM calls + outreach, priced from published rates: about ₹11.60 to recover
₹96,433 — roughly ₹0.01 per ₹100 recovered. Also shown on the dashboard next
to the resolution bar.

## 11. Proof, not vibes

```
python -m pytest
```

59 tests on the money-path invariants: fraud is never retried regardless of
LLM output and has no side door through the reply channel, settled payments
can't be reopened, the amount cap holds, attempt 4 is impossible, crash
reconciliation never duplicates, refusals always stop contact, injection
payloads are screened without a model call, and webhooks reject bad
signatures without writing anything.

## 12. Read the result

Dashboard shows the final split: recovered / escalated / written off / let go,
the recovery rate per failure class and per payment method, banks on hold, and
every payment's full audit trail.

## Honest-simulation disclosure

Orders and payment links are real Razorpay test-mode API objects. Payment
*outcomes* (does the simulated customer pay / keep a promise) are simulated
with seeded RNG, because test mode has no humans. The Razorpay account's
lifetime cap of 30 test-mode payment links is exhausted, so link creation
degrades gracefully to simulated delivery — disclosed per-action in the
audit trail (`link_quota_fallback`).
