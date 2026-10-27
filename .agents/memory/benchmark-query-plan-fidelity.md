---
name: Benchmark query-plan fidelity
description: Keep benchmark query plans representative of the live endpoint when request filtering changes.
---

Benchmark `EXPLAIN` queries should call the same reusable queryset/filter builder as the corresponding endpoint, rather than independently restating its ORM conditions.

**Why:** A duplicated benchmark query can keep explaining an old SQL shape after the production request is optimized, producing misleading plan evidence.

**How to apply:** When adding or changing benchmark `EXPLAIN` coverage, extract shared authorization-safe query construction or call the endpoint's builder, then confirm the measured path and explained path use identical parameters.

Do not equate a PostgreSQL active-backend sample with concurrent ERP requests. Verify request overlap and use equivalent scheduling/instrumentation for before/after comparisons.

**Why:** Authenticated requests alternate between Python/session/permission work and short SQL statements. A Python-thread sampler can miss SQL peaks or be delayed by the GIL; changing workload scheduling alone can change tails without an application improvement.

**How to apply:** Record request start/end and actual peak in-flight counts, synchronize bounded endpoint waves when testing simultaneous demand, and corroborate SQL activity with an independent process when needed. Distinguish server execution times from Python SQL-wrapper wall times, and do not attribute historical non-reproduced tails to a new query fix.

Keep whole-suite disposable test environments compatible with the existing restore/demo-fixture safety guards rather than relaxing those guards to accept a benchmark naming context.

**Why:** The full suite deliberately validates synthetic-only command boundaries; a benchmark-safe database name is not necessarily a guard-compatible whole-suite test name.

**How to apply:** Inspect the current guard requirements before running the whole suite on a separate temporary PostgreSQL instance. Correct its test-environment naming/configuration, never the shared preview database or safety policy.
