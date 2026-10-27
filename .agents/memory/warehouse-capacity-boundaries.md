---
name: Warehouse capacity boundaries
description: Product constraints for synthetic per-location capacity tracking and authorization.
---

Capacity values are synthetic-only and belong to an explicitly designated physical location. A profile is the explicit designation; do not infer physical location type from the location tree.

Viewing/editing requires a separate per-user, per-location capacity assignment. Staff status, stock access, purchasing roles, and report grants do not imply capacity access. Do not seed capacity assignments for demo users.

A capacity assignee must not need an additional stock/purchasing BU grant just to reach the capacity UI. An unavailable organization/stock context must not hide independently authorized capacity.

**Why:** The owner specified that only designated warehouse managers may view/edit capacity and that ERP/system administrators assign access per physical location; ordinary stock access must not reveal capacity. Conversely, wrapping capacity in a stock-context guard can accidentally deny a legitimate independent assignment.

**How to apply:** Keep capacity permission checks and UI availability independent from business-unit action grants. Gate stock/receiving on their own context, but retain the capacity API's explicit-location assignment path. Keep floor area in m² and received-goods volume in m³; do not convert between them or infer volumetric availability. Do not couple these manual fields to receipts, stock balances, or valuation without explicit direction.

Administrative designation, assignment and audit screens should use metadata
only, not fetch capacity measurements merely to reuse warehouse controls.

**Why:** The approved Governance workspace arranges assignment authority; it
does not give administrators an implicit location-capacity viewing assignment.
Mixing the two presentations obscures that deliberate separation.

**How to apply:** Keep Governance administration free of measurement reads and
values; retain measurement viewing/editing in the independently assigned
warehouse path. Do not broaden either capability while rearranging navigation.
