---
name: Authorization test evidence
description: Avoid false disclosure findings from status-only assertions or shared-location assumptions.
---

Evaluate authorization using the actual scoped records and protected values,
not HTTP status alone. A permitted response containing empty assignment context
does not grant access to protected measurements or financial facts.

**Why:** A bounded disclosure review initially treated safe metadata responses
as denied-data failures. It also assumed a location associated with another
brand could not legitimately have explicit membership in the selected unit.
Those assumptions produced false findings, not application leaks.

**How to apply:** Establish the endpoint's existing contract; verify field and
row scope directly. For cross-unit denial, use fixtures whose explicit
memberships really exclude the selected unit. Keep a separate case for
legitimately shared locations. Do not broaden or restrict access merely to
satisfy an incorrect test expectation.

For an action-only denial case, positively establish that the same persona can
still read the exact record. Preserve every existing prerequisite for that
read; do not assume retaining one grant is sufficient.

**Why:** Removing the final synthetic approval role also invalidated quotation
reads under the existing contract. That was real read-access loss, not evidence
that the UI incorrectly erased an authorized quotation. An isolated downgrade
to an existing lower role can instead preserve read prerequisites while removing
the tested higher approval authority, without creating a new role or policy.

**How to apply:** Check actual read responses and record scope alongside the
denied action. Keep confirmed read loss, action-only denial and failed read
verification as separate cases and outcomes.
