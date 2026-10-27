---
name: Packing-list catalog scope
description: Safe interpretation of item-code matches in the local supplier packing-list preview.
---

Keep direct packing-list item matching limited to records exposed by the user's existing selected-business-unit stock authorization until an explicit business-unit-to-item catalog relationship and access scope are approved. A missing match means “not present in the authorized current ledger,” not “the item does not exist.” Explicit commitment preparation can use variants already disclosed by authorized commitment progress, without requiring a ledger bucket or searching the global catalog.

**Why:** The prototype has no approved business-unit-to-master-catalog relationship. Searching all items or inferring access from brand could expose items outside the user's intended scope. A user-selected authorized commitment is an existing scoped document reference, so requiring received stock first would incorrectly prevent receiving its unstocked lines.

**How to apply:** Do not broaden matching to the global item table or infer business-unit membership. If unstocked catalog items must match, obtain an explicit catalog ownership/access decision before changing the endpoint or UI wording.

Keep direct packing-list preparation separate from explicit commitment-line receiving; never infer a supplier or commitment from an item code.

**Why:** The same item can appear on multiple commitments. Guessing the link could bypass the intended per-line remaining-quantity/over-receipt safeguards while appearing to reconcile a commitment.

**How to apply:** Any future commitment-aware preparation must ask for an authorized exact commitment/line and retain existing over-receipt checks; it must not silently turn an unmatched commitment into a direct receipt.

Leaving a receipt-preparation context invalidates its review, rather than merely hiding the copied draft.

**Why:** A deterministic owner key can match again after A→B→A, resurrecting an old reviewed draft even though the current mapping controls require fresh choices. A rendered round-trip regression reproduced this.

**How to apply:** Clear local reviewed state when context ownership changes while preserving the independent pending POST's legitimate outcome and retry identity.
