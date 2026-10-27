---
name: Purchasing policy boundaries
description: User-approved synthetic purchasing and delivery rules, plus decisions that must remain unconfigured until explicitly approved.
---

For the synthetic retail prototype, discount authority is configurable per business unit: Salesperson 10%, Manager 20%, Commercial GM 30%, and CEO exceptional 50%. Do not preassign real named people. Calculate discounts from the approved selling-price snapshot on each quote; never change the source price.

Seed the synthetic work calendar with Friday closed and Saturday/Sunday working. Temporary holds may last no more than three working days. An in-stock reservation requires at least a 50% non-returnable deposit; a 40% special-order payment is not enough to reserve stock. Special orders may use full payment or a per-order 40%-70% downpayment with an explicit approver and reason. Require full payment before ordinary delivery unless a BU GM or CEO explicitly approves a 5%-10% post-handover balance with a reason.

Provide one free storage month from readiness notification, then charge 1% of total quote value monthly and retain auditable notifications and exception approvals. Special-order value bands remain unconfigured until approved; do not invent thresholds. If a policy capability needs a missing approver, price, grant, calendar, or accounting mapping, fail closed.

**Why:** These are explicitly scoped prototype inputs, not approval for real Tivoli transactions, accounting, named-person authority, or production use. The user also requires seed/idempotency checks to remain isolated because the demo seeder can create baseline users and read-stock grants.

**How to apply:** For future purchasing or finance work, keep the supplied numeric rules separate from unapproved value bands, taxes, payment accounting, storage billing conventions, and role assignments. Use synthetic fixtures only and state unresolved policy decisions. Run seed checks only in disposable PostgreSQL test databases; do not invoke seed commands against shared development or production data.

Until Delivery & After-Sales business acceptance is complete, prioritize its
already-approved operational gaps over additional monitoring utilities. A
missing Finance/access decision blocks only the dependent behavior, not
independent delivery work.

**Why:** On 2026-10-19 the owner explicitly refocused development on this
business section after several monitoring increments.

**How to apply:** Inspect approved requirements and existing services first;
implement independently determined operations without new policy/assignments,
and state the exact unresolved decision for partial-delivery or financial-return
behavior instead of inventing it or substituting another monitoring feature.

Collecting an approved deferred quote balance after handover must append payment/idempotency facts without changing an existing delivered or readiness state.

**Why:** Delivery/readiness and settlement are separate facts; a reservation-related status update must not rewrite a completed handover when a balance is recorded.

**How to apply:** Keep post-handover state transitions limited to explicitly supported pre-delivery stages. Verify the quote status and readiness/delivery timestamps remain stable after the payment and an idempotent retry, with no duplicate payment or reservation facts.

The owner approved ordinary authorized nonstaff sales selecting existing items
and customer choices needed for quotations, fixed approved prices and only
their existing grade's discounts. This does not approve procurement/supplier
access, item creation by sales, broader customer management or administrative
configuration access.

**Why:** The owner resolved the previously pending minimum nonstaff quotation
choices decision on 2026-10-20, independently of the still-pending procurement
access and temporary-hold stock-blocking questions.

**How to apply:** Keep quotation choices separate from full staff governance and
directory controls. Displayed bilingual payment/delivery/validity notes are
explicit presentation inputs, never substitutes for actual commercial gates.
Terms and Conditions require owner-supplied text; never invent real translations
or legal wording.

Submitted transactions must continue to completion across business-unit or quote navigation; ignore stale screen callbacks, not the transaction's outcome. An unseen success must not be mistaken for a cancelled operation. Changing quotes must not suppress still-current business-unit authorization rechecks.

**Why:** The user explicitly requires POSTs to finish while stale UI callbacks are guarded, with understandable server outcomes and retry identities. Cancelling a browser request would not prove a server-side rollback; suppressing a valid unit-level access recheck could hide permission revocation.

**How to apply:** Do not copy abort-on-navigation GET patterns into transaction submissions. Preserve the original identity for exact, unacknowledged retries, separate quote-display ownership from still-current unit authorization checks, and keep existing payment/order rules unchanged.

Temporary-hold availability is awaiting the owner's clarification: the owner has
asked whether held quantities must block other sales. Leave the current
intent-only behavior unchanged until that answer is supplied.

**Why:** On 2026-10-19 the owner explicitly separated documenting the observed
hold behavior from approving stock-blocking semantics.

**How to apply:** Mark this as an unresolved company decision in acceptance
notes/checklists. Do not silently convert a hold into a ledger reservation or
claim that the present hold prevents competing sales.

An operational quote collection balance is not an accounting receivable,
overdue assessment or approval of credit terms. Keep merchandise collections
separate from storage collections and retain the existing payment gates.

**Why:** The owner explicitly authorized a read-only synthetic follow-up
worklist while excluding new due dates, credit/refund policy and financial
approval. A positive remaining value does not itself authorize collection.

**How to apply:** Future Retail/reporting extensions must preserve that
distinction; request approval before adding aging, due-date or credit behavior,
and never use storage collections to settle merchandise balances.
