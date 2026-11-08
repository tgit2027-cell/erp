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
    ("retail.customers", "Retail · Customers"),
    ("retail.commission", "Retail · My commission"),
    ("procurement.commitments", "Procurement · Create purchase commitment"),
    ("procurement.progress", "Procurement · Commitment receipt progress"),
    ("procurement.receipt", "Procurement · Prepare imported-goods receipt"),
    ("procurement.packing", "Procurement · Packing-list mapping & review"),
    ("procurement.registers", "Procurement · Receipt & landed-charge registers"),
    ("procurement.suppliers", "Procurement · Staff counterparty directory"),
    ("procurement.kyc", "Procurement · Supplier onboarding (KYC)"),
    ("finance.supplier-payments", "Finance · Supplier payments"),
    ("finance.ledger", "Finance · General ledger"),
    ("finance.refunds", "Finance · Customer refunds"),
    ("finance.receivables", "Finance · Who owes us (receivables)"),
    ("finance.payables", "Finance · What we owe (payables)"),
    ("finance.supplier-bills", "Finance · Supplier bills"),
    ("finance.cash", "Finance · Bank and cash"),
    ("finance.bank-rec", "Finance · Bank reconciliation"),
    ("finance.expenses", "Finance · Expenses and petty cash"),
    ("finance.budgets", "Finance · Budgets"),
    ("finance.assets", "Finance · Fixed assets"),
    ("finance.journals", "Finance · Journal entries"),
    ("finance.notes", "Finance · Credit and debit notes"),
    ("finance.advances", "Finance · Supplier advances"),
    ("finance.forecast", "Finance · Cash-flow forecast"),
    ("finance.payment-plan", "Finance · Weekly payments"),
    ("finance.sharing", "Finance · Cost sharing"),
    ("finance.cheques", "Finance · Cheques"),
    ("hr.leave", "HR · Leave"),
    ("hr.attendance", "HR · Attendance"),
    ("hr.overtime", "HR · Overtime"),
    ("hr.pay", "HR · Salaries"),
    ("hr.payroll", "HR · Payroll"),
    ("hr.incentives", "HR · Commission and rewards"),
    ("hr.end-of-service", "HR · End of service"),
    ("hr.loans", "HR · Loans and advances"),
    ("hr.letters", "HR · Letters"),
    ("inventory.locations", "Inventory · Permitted locations"),
    ("inventory.receipt", "Inventory · Prepare imported-goods receipt"),
    ("inventory.commitments", "Inventory · Purchase commitments"),
    ("inventory.progress", "Inventory · Commitment receipt progress"),
    ("inventory.packing", "Inventory · Packing-list preview"),
    ("inventory.registers", "Inventory · Inventory & transfer registers"),
    ("inventory.transfers", "Inventory · Stock transfer requests"),
    ("inventory.capacity", "Inventory · Warehouse capacity"),
    ("inventory.counts", "Inventory · Stock counts"),
    ("inventory.low-stock", "Inventory · Low stock"),
    ("inventory.stock-value", "Inventory · Stock value"),
    ("inventory.items", "Inventory · Stock items"),
    ("inventory.catalogue", "Inventory · Read a supplier catalogue"),
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
    ("governance.users", "Governance · Users and permissions"),
    ("governance.imports", "Governance · Go-live imports"),
    ("purchasing.quote", "Purchasing · Quote lookup"),
    ("purchasing.transactions", "Purchasing · Transaction workbench"),
    ("purchasing.registers", "Purchasing · Purchasing records"),
    ("reports.reconciliation", "Reports · Scenario reconciliation"),
    ("reports.audit", "Reports · Audit log"),
]


def function_is_available(destination, *, is_staff, is_superuser, manages_panels, reads_stock, manages_users=None, imports=None):
    """Visibility matches existing UI gates, not authority to use the function."""
    if destination in {
        "procurement.suppliers", "governance.presentation",
        "governance.purchasing-config", "governance.reporting",
        "reports.reconciliation",
    }:
        return is_staff
    if destination == "governance.report-panels":
        return manages_panels
    if destination in {"governance.users", "governance.transfer-permissions"}:
        # Master Admins (system administrators) and General Admins.
        return is_superuser if manages_users is None else manages_users
    if destination == "governance.imports":
        return is_superuser if imports is None else imports
    if destination in {
        "governance.capacity-admin", "governance.capacity-audit", "reports.audit",
    }:
        return is_superuser
    # Capacity and transfer requests carry their own permissions, separate from stock reading.
    if destination.startswith("inventory.") and destination not in {"inventory.capacity", "inventory.transfers"}:
        return reads_stock
    return True
