# Submission draft

Working draft of the application-form answers and the pitch-video script.
(Not part of the product — kept in the repo so nothing gets lost.)

## Form answers

**Track:** AI Revenue Recovery (Track 3)

**Project name:** Reclaim

**Project objectives / what it solves:**

> A large share of Indian online payments fail — insufficient funds, expired
> cards, UPI timeouts, abandoned checkouts — and most merchants never follow
> up, so the revenue just evaporates. Reclaim is an agent that detects
> at-risk revenue in a payment batch, diagnoses the root cause of each
> failure with an LLM, chooses a bounded intervention through a deterministic
> policy engine (the LLM advises, it never moves money), executes it via
> Razorpay APIs with idempotency keys, and tracks customers' promise-to-pay
> replies in Hinglish. On the demo batch it recovered ₹96,433 of ₹112,865
> at-risk revenue (85%), escalated 6 payments to a human under its stopping
> rules, and wrote one off because the customer refused — compliance beats
> recovery rate. Every action is recorded in an append-only audit trail with
> its reasoning, and the whole run is crash-safe: kill the executor mid-batch
> and it reconciles against Razorpay on restart with zero double-charges.

**GitHub repo:** https://github.com/jaidevxb/razorpay-buildathon

**Build challenges & technical obstacles** (the long answer is CHALLENGES.md
in the repo; condensed):

> The most instructive bug came from Razorpay's test-mode rate limiter. My
> seeding loop crashed mid-batch, and because the whole batch ran in one
> SQLite transaction, the rollback erased local records of orders that had
> already been created on Razorpay's side — local state diverged from the
> external world, which in a real money system is exactly how double-charges
> happen. The fix reshaped the architecture: commit per action immediately
> after its external call, mark actions in-flight *before* calling out, and
> reconcile in-flight actions against Razorpay by reference id on restart.
> That reconciliation path later got exercised by a real crash before I ever
> staged one. Test mode also hides a lifetime cap of 30 payment links —
> cancelling doesn't refund it — so the executor learned graceful
> degradation: it continues with clearly-disclosed simulated link delivery
> instead of halting the batch. And my first version of that fallback burned
> a full 60-second backoff cycle per action to rediscover the same quota
> error — a retry storm against myself — fixed by memoizing the quota state
> for the process lifetime.

## 5-minute video script

**0:00–0:45 — Problem.** Screen: dashboard KPI row. "Out of 100 payments this
merchant tried to collect, 45 failed — ₹1.1 lakh just sitting there. Most
merchants never follow up. Reclaim wins it back, safely."

**0:45–2:00 — The pipeline, live.** Terminal + dashboard side by side. Reset,
seed (show the same orders appearing in the Razorpay test dashboard), detect,
diagnose. Point at one payment's detail page: Gemini's root cause and drafted
message. "Detection is plain SQL — no AI where a database query is the right
tool. Diagnosis is where the LLM earns its place."

**2:00–2:45 — Policy: the LLM advises, rules decide.** Show the two
fraud-suspected payments jumping straight to *escalated*. Open one audit
trail: "hard rule: risk-engine declines are never retried automatically —
overrides any LLM advice."

**2:45–3:45 — Kill and resume.** Start the executor, let the recovered number
climb, **Ctrl+C mid-run**. Show the action frozen in `executing`. Restart.
Show the `reconciling_in_flight_action` audit entry. "Nothing double-charged.
Crash-safety isn't a slide in this deck, you just watched it."

**3:45–4:30 — Promises.** Show the Customer promises panel: "salary aane do,
Friday ko pakka kar dunga" → Gemini parses the Hinglish, policy tracks it.
Point at the refusal: "This customer said no. We stopped contacting them and
wrote it off. Compliance beats recovery rate."

**4:30–5:00 — Close.** Final dashboard. "₹96,433 of ₹112,865 recovered —
85% — with stopping rules, full audit trail, and honest disclosure of what's
simulated. Everything that broke building this is in CHALLENGES.md, because
that was the most educational part."
