"""Favorite navigation choices, never data/transaction authorization.

Keep the function keys aligned with the implemented sidebar route catalog.
Section favorites remain backward compatible. Legacy route aliases are not
additional favorite choices; they resolve to their canonical function.
"""

FUNCTION_DESTINATIONS = [
    ("retail.quote", "Retail · Create a quote"),
    ("retail.actions", "Retail · Quote collection & holds"),
    ("retail.register", "Retail · Authorized quote register"),
    ("retail.collections", "Retail · Collection follow-up"),
    ("retail.invoices", "Retail · Customer invoices"),
    ("procurement.commitments", "Procurement · Create purchase commitment"),
    ("procurement.progress", "Procurement · Commitment receipt progress"),
    ("procurement.receipt", "Procurement · Prepare imported-goods receipt"),
    ("procurement.packing", "Procurement · Packing-list mapping & review"),
    ("procurement.registers", "Procurement · Receipt & landed-charge registers"),
    ("procurement.suppliers", "Procurement · Staff counterparty directory"),
    ("procurement.kyc", "Procurement · Supplier onboarding (KYC)"),
    ("inventory.locations", "Inventory · Permitted locations"),
    ("inventory.receipt", "Inventory · Prepare imported-goods receipt"),
    ("inventory.commitments", "Inventory · Purchase commitments"),
    ("inventory.progress", "Inventory · Commitment receipt progress"),
    ("inventory.packing", "Inventory · Packing-list preview"),
    ("inventory.registers", "Inventory · Inventory & transfer registers"),
    ("inventory.transfers", "Inventory · Stock transfer requests"),
    ("inventory.capacity", "Inventory · Warehouse capacity"),
    ("delivery.quote", "Delivery · Authorized quote lookup"),
    ("delivery.preparation", "Delivery · Picking preparation"),
    ("delivery.handover", "Delivery · Readiness & handover actions"),
    ("delivery.quotes", "Delivery · Quote register"),
    ("delivery.storage", "Delivery · Storage-charge register"),
    ("delivery.reservations", "Delivery · Reservation register"),
    ("delivery.deliveries", "Delivery · Delivery register"),
    ("delivery.returns", "Delivery · Sales-return intake"),
    ("governance.presentation", "Governance · Business-unit palettes"),
    ("governance.purchasing-config", "Governance · Purchasing configuration review"),
    ("governance.reporting", "Governance · Reporting governance"),
    ("governance.report-panels", "Governance · Manage report panels"),
    ("governance.capacity-admin", "Governance · Capacity locations & assignments"),
    ("governance.capacity-audit", "Governance · Warehouse capacity audit"),
    ("governance.transfer-permissions", "Governance · Stock transfer permissions"),
    ("purchasing.quote", "Purchasing · Quote lookup"),
    ("purchasing.transactions", "Purchasing · Transaction workbench"),
    ("purchasing.registers", "Purchasing · Purchasing records"),
    ("reports.reconciliation", "Reports · Scenario reconciliation"),
    ("reports.audit", "Reports · Audit log"),
]


def function_is_available(destination, *, is_staff, is_superuser, manages_panels, reads_stock):
    """Visibility matches existing UI gates, not authority to use the function."""
    if destination in {
        "procurement.suppliers", "governance.presentation",
        "governance.purchasing-config", "governance.reporting",
        "reports.reconciliation",
    }:
        return is_staff
    if destination == "governance.report-panels":
        return manages_panels
    if destination in {
        "governance.capacity-admin", "governance.capacity-audit", "governance.transfer-permissions", "reports.audit",
    }:
        return is_superuser
    # Capacity and transfer requests carry their own permissions, separate from stock reading.
    if destination.startswith("inventory.") and destination not in {"inventory.capacity", "inventory.transfers"}:
        return reads_stock
    return True
