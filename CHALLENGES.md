# Build Challenges & Technical Obstacles

A running log of what actually broke while building this, and how each problem
was solved. Kept from day one — newest entries at the bottom.

---

## 2026-08-22 — Razorpay test mode rate-limited the simulator

**What broke:** Seeding 100 payments fired 100 `order.create` calls in a tight
loop. Razorpay test mode replied `BadRequestError: Too many requests` partway
through and the simulator crashed.

**Fix:** Added a 250ms gap between requests plus exponential backoff (1s, 2s,
4s... up to 5 attempts) when the rate limit is hit anyway.

## 2026-08-22 — Batch transaction rolled back rows for orders that already existed

**What broke:** The whole seeding loop ran inside one SQLite transaction. When
the rate-limit crash happened, SQLite rolled back *every* insert — but the
Razorpay orders created before the crash are real external side effects that
can't be rolled back. Result: orders existed on Razorpay's dashboard with zero
local record of them. In a real money system this divergence is how you
double-charge someone.

**Fix:** Commit per payment, immediately after its external API call succeeds.
The database may end up with fewer rows than requested after a crash, but every
row it has is truthful, and idempotent re-runs fill the gap without duplicates.
This "local record must survive if the external side effect happened" rule
becomes the core design principle for the recovery executor too.

## 2026-08-22 — Payment-link API is rate-limited much harder than orders

**What broke:** The executor crashed on `Too many requests` even with the
backoff that had fixed the same problem for order creation. Each action was
also making two API calls (an "does this link already exist" lookup plus the
create), doubling pressure on the stricter limit.

**Fix:** Backoff wraps every Razorpay call now, with waits capped at 60s. And
the existence lookup only runs on the crash-reconciliation path — a freshly
planned action cannot have a pre-existing link, so checking was pure waste.

**Silver lining:** the crash left one action stuck in `executing`, and the next
run's reconciliation logic resolved it correctly against Razorpay by reference
id — the crash-recovery path got tested by a real crash before we ever staged
a fake one.

## 2026-08-22 — Test mode allows only 30 payment links, total

**What broke:** Partway through the batch, Razorpay returned
`ServerError: test mode limit of 30 reached for payment_link`. Not a rate
limit — a hard quota on links in test mode.

**Fix:** Cancel each link as soon as its attempt resolves. This turned out to
be the correct design regardless of quota: when attempt 1's link stays live
and attempt 2 issues a new link for the same debt, a customer who later opens
the stale link can pay twice. The quota error forced us into behaviour a real
money system needs anyway.
