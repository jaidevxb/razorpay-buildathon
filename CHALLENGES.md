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
