---
name: Synthetic integrity diagnostics
description: Keep offline checks snapshot-consistent and separate from repair or application-user authorization.
---

Run multi-query integrity diagnostics against a single database-enforced repeatable-read, read-only snapshot, not independent read-committed reads.

**Why:** An atomic posting can commit between the balance read and source-fact aggregation. Even though both generations are individually correct, mixing them creates false discrepancy alerts; a real two-connection transfer regression demonstrated this risk.

**How to apply:** Extend the existing local synthetic operator boundary for future transfer/journal checks, retain a consistent snapshot, and keep diagnoses separate from repairs, new app-user capabilities and unapproved opening-balance or financial assumptions.

Do not reconstruct historical transfer authorization using today's grants or location memberships.

**Why:** These assignments can change independently after posting; treating current membership as historical approval evidence would introduce a retroactive rule that the user has not approved.

**How to apply:** Structural diagnostics compare persisted document references and quantities. Historical approval/access proofs need their own approved evidence and scope, separate from these checks.

A combined operator summary may run independently snapshot-consistent checks
sequentially, but must explicitly say there is no shared snapshot and must not
compare generations across checks.

**Why:** Reusing the unchanged diagnostic transaction boundaries allows one SQL
failure to roll back without disabling the other checks. A combined report is
not automatically a point-in-time cross-ledger reconciliation.

**How to apply:** Preserve each check's read-only repeatable-read contract,
distinguish execution failure from zero discrepancies, and do not wrap these
checks in an outer transaction or imply cross-check snapshot consistency.
