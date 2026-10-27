"""Read-only reconciliation of the purchase-to-invoice scenario for one business unit.

Each check compares two independently stored records of the same fact, for
example the stock ledger against the journal, so a mismatch points at the
step that went wrong. It never repairs anything. Amounts are QAR.
"""

from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Count, Sum

from .models import (
    CustomerInvoice,
    CustomerInvoiceLine,
    JournalLine,
    PurchaseReceipt,
    PurchaseReceiptCharge,
    QuotePayment,
    SalesQuote,
    SalesReportLine,
    StockBalance,
    StockMovement,
)

ZERO = Decimal("0.00")
POLICY_LABEL = (
    "TEST ONLY reconciliation under the synthetic test policy: single cost layer, "
    "revenue at delivery, deposits held as a liability until delivery, no tax. "
    "Not an approved accounting report."
)


def _total(queryset, field):
    return queryset.aggregate(total=Sum(field))["total"] or ZERO


def _ledger(unit, code, *, kind=None):
    lines = JournalLine.objects.filter(
        entry__business_unit=unit, entry__status="posted", account__code=code, is_synthetic=True,
    )
    if kind is not None:
        lines = lines.filter(entry__kind=kind)
    return _total(lines, "debit"), _total(lines, "credit")


def _money(value):
    return format(value, ".2f")


def build_scenario_reconciliation(business_unit):
    """Return the summary figures and each check with its two sides."""
    if not connection.in_atomic_block:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return _build(business_unit)
    return _build(business_unit)


def _build(unit):
    receipts = PurchaseReceipt.objects.filter(business_unit=unit, is_synthetic=True)
    movements = StockMovement.objects.filter(balance__business_unit=unit, is_synthetic=True)
    balances = StockBalance.objects.filter(business_unit=unit, is_synthetic=True)
    sales = SalesReportLine.objects.filter(business_unit=unit, is_synthetic=True)
    invoices = CustomerInvoice.objects.filter(business_unit=unit, is_synthetic=True)
    merchandise = QuotePayment.objects.filter(
        quote__business_unit=unit, is_synthetic=True,
        kind__in=[QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE],
    )

    supplier_value = _total(receipts, "purchase_value")
    landed_charges = _total(
        PurchaseReceiptCharge.objects.filter(receipt__in=receipts, is_eligible=True), "amount"
    )
    received_value = _total(receipts, "inventory_value")
    receipt_movements = _total(movements.filter(kind=StockMovement.Kind.RECEIPT), "value_delta") + _total(
        movements.filter(kind=StockMovement.Kind.LANDED_COST), "value_delta"
    )
    receipt_inventory_debit, _ = _ledger(unit, "TEST_INVENTORY", kind="receipt")
    _, grni_credit = _ledger(unit, "TEST_GRNI", kind="receipt")
    _, landed_credit = _ledger(unit, "TEST_LANDED_CLEARING", kind="receipt")

    stock_value = _total(balances, "on_hand_value")
    movement_value = _total(movements, "value_delta")
    inventory_debit, inventory_credit = _ledger(unit, "TEST_INVENTORY")
    stock_quantity = _total(balances, "on_hand_quantity")
    movement_quantity = _total(movements, "quantity_delta")

    revenue = _total(sales, "revenue")
    cogs = _total(sales, "cost_of_goods_sold")
    _, ledger_revenue = _ledger(unit, "TEST_REVENUE")
    ledger_cogs, _ = _ledger(unit, "TEST_COGS")
    delivery_movement_value = -_total(movements.filter(kind=StockMovement.Kind.DELIVERY), "value_delta")
    receivable_debit, _ = _ledger(unit, "TEST_RECEIVABLE", kind="delivery")

    invoiced_net = _total(invoices, "net_total")
    invoiced_total = _total(invoices, "invoice_total")
    invoice_line_total = _total(CustomerInvoiceLine.objects.filter(invoice__in=invoices), "line_total")
    delivered_quotes = SalesQuote.objects.filter(
        business_unit=unit, is_synthetic=True, delivered_at__isnull=False,
    )
    delivered_quote_value = _total(delivered_quotes, "total_quote_value")
    paid_before_delivery = _total(invoices, "paid_before_delivery")
    balance_due_at_delivery = _total(invoices, "balance_due")
    invoiced_payments = merchandise.filter(quote__invoice__in=invoices)
    collected_on_invoices = _total(invoiced_payments, "amount")
    still_to_collect = max(invoiced_total - collected_on_invoices, ZERO)
    quotes_without_invoice = delivered_quotes.filter(invoice__isnull=True).count()
    invoices_without_lines = invoices.annotate(line_count=Count("lines")).filter(line_count=0).count()

    all_payments = _total(merchandise, "amount")
    cash_debit, cash_credit = _ledger(unit, "TEST_CASH")
    deposits_held = _total(merchandise.filter(quote__delivered_at__isnull=True), "amount")
    deposit_debit, deposit_credit = _ledger(unit, "TEST_CUSTOMER_DEPOSITS")
    receivable_total_debit, receivable_total_credit = _ledger(unit, "TEST_RECEIVABLE")

    all_lines = JournalLine.objects.filter(entry__business_unit=unit, entry__status="posted", is_synthetic=True)
    journal_debits, journal_credits = _total(all_lines, "debit"), _total(all_lines, "credit")

    checks = [
        ("receipts", "Received stock value equals supplier value plus eligible landed charges",
         supplier_value + landed_charges, received_value),
        ("receipts", "Stock ledger received the same value as the receipts",
         received_value, receipt_movements),
        ("receipts", "Journal debited inventory with the same receipt value",
         received_value, receipt_inventory_debit),
        ("receipts", "Journal credited supplier value and landed charges separately",
         supplier_value + landed_charges, grni_credit + landed_credit),
        ("stock", "Stock on hand value equals the sum of all stock movements",
         movement_value, stock_value),
        ("stock", "Stock on hand quantity equals the sum of all stock movements",
         movement_quantity, stock_quantity, "quantity"),
        ("stock", "Inventory account balance equals stock on hand value",
         stock_value, inventory_debit - inventory_credit),
        ("deliveries", "Revenue in sales records equals revenue in the journal",
         revenue, ledger_revenue),
        ("deliveries", "Cost of goods sold in sales records equals the journal",
         cogs, ledger_cogs),
        ("deliveries", "Cost of goods sold equals the stock value that left at delivery",
         cogs, delivery_movement_value),
        ("invoices", "Delivered quotes without an invoice", 0, quotes_without_invoice),
        ("invoices", "Invoices without lines", 0, invoices_without_lines),
        ("invoices", "Invoiced net value equals the delivered quote values",
         delivered_quote_value, invoiced_net),
        ("invoices", "Invoice lines add up to the invoice net values",
         invoiced_net, invoice_line_total),
        ("invoices", "Invoiced net value equals revenue recognised at delivery",
         invoiced_net, revenue),
        ("invoices", "Receivable raised at delivery equals revenue",
         revenue, receivable_debit),
        ("payments", "Paid before delivery plus balance due equals the invoice totals",
         invoiced_total, paid_before_delivery + balance_due_at_delivery),
        ("payments", "Collections recorded on invoiced quotes cover at least what was paid before delivery",
         paid_before_delivery, min(collected_on_invoices, paid_before_delivery)),
        ("payments", "Cash in the journal equals all customer payments recorded",
         all_payments, cash_debit - cash_credit),
        ("payments", "Deposits owed to customers equal payments on quotes not yet delivered",
         deposits_held, deposit_credit - deposit_debit),
        ("payments", "Receivable balance equals what customers still owe on invoices",
         still_to_collect, receivable_total_debit - receivable_total_credit),
        ("journals", "Posted journal debits equal credits",
         journal_debits, journal_credits),
    ]
    rows = [{
        "area": area,
        "label": label,
        "unit": unit[0] if unit else "count" if isinstance(expected, int) else "QAR",
        "expected": str(expected) if isinstance(expected, int) else _money(expected),
        "actual": str(actual) if isinstance(actual, int) else _money(actual),
        "difference": str(actual - expected) if isinstance(expected, int) else _money(actual - expected),
        "matches": actual == expected,
    } for area, label, expected, actual, *unit in checks]
    return {
        "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
        "currency": "QAR",
        "summary": {
            "supplier_value": _money(supplier_value),
            "landed_charges": _money(landed_charges),
            "received_value": _money(received_value),
            "stock_value": _money(stock_value),
            "revenue": _money(revenue),
            "cost_of_goods_sold": _money(cogs),
            "gross_margin": _money(revenue - cogs),
            "invoiced_total": _money(invoiced_total),
            "collected_on_invoices": _money(collected_on_invoices),
            "still_to_collect": _money(still_to_collect),
            "deposits_held": _money(deposits_held),
            "invoice_count": invoices.count(),
        },
        "checks": rows,
        "all_match": all(row["matches"] for row in rows),
        "policy_label": POLICY_LABEL,
    }
