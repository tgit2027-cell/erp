"""Who owes the business unit money: receivables aging and customer statements.

Read-only. What a customer owes on an invoice is the invoice total, less every
deposit and balance payment taken on its quote, less the part of any credit
note that was applied to the invoice. An invoice's age is counted from its
date (the delivery date): payment terms vary by customer, so there is no due date (owner decision).

Aging is by days (0-30, 31-60, 61-90, 91-120, over 120) or by years (up to
five years, then older). A statement shows either every transaction or only
the invoices still unpaid.

The full statement lists everything that moved the customer's balance with the unit:
payments (including deposits taken before delivery), invoices, credit notes
and refunds paid out. A negative balance means the unit holds the customer's
money, for example a deposit on an order not yet delivered.

Both need the 'view general ledger' permission for the business unit.
"""

import datetime
from decimal import Decimal

from django.utils import timezone

from .general_ledger import _day, _require_viewer
from .models import AdjustmentNote, CustomerCreditNote, CustomerInvoice, OldBalance, OldBalanceSettlement, QuotePayment, SyntheticCounterparty
from .transaction_services import InvalidTransaction

ZERO = Decimal("0.00")
BUCKETS = (("current", "0–30 days", 0, 30), ("days_31_60", "31–60 days", 31, 60),
           ("days_61_90", "61–90 days", 61, 90), ("days_91_120", "91–120 days", 91, 120),
           ("over_120", "Over 120 days", 121, None))
# The same aging by years, for older balances: up to five years, then anything older.
YEAR_BUCKETS = (("year_1", "Up to 1 year", 0, 365), ("years_1_2", "1–2 years", 366, 730),
                ("years_2_3", "2–3 years", 731, 1095), ("years_3_4", "3–4 years", 1096, 1460),
                ("years_4_5", "4–5 years", 1461, 1825), ("over_5_years", "Over 5 years", 1826, None))
BUCKET_SETS = {"days": BUCKETS, "years": YEAR_BUCKETS}
OWED_KINDS = (QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE)
POLICY_LABEL = (
    "TEST ONLY: ages are counted from the invoice date, as payment terms vary by customer. "
    "Storage charges are not included."
)


def _money(value):
    return format((value or ZERO).quantize(Decimal("0.01")), "f")


def _local_day(moment):
    return timezone.localtime(moment).date()


def _end_of(day):
    return timezone.make_aware(datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time.min))


def _buckets(by):
    if by in (None, ""):
        return BUCKETS
    if by not in BUCKET_SETS:
        raise InvalidTransaction("Choose aging by days or by years.")
    return BUCKET_SETS[by]


def _bucket(age, buckets=BUCKETS):
    for key, _, low, high in buckets:
        if age >= low and (high is None or age <= high):
            return key
    return buckets[0][0]


def _customer(customer):
    return {
        "id": customer.pk, "name": customer.name, "title": customer.get_title_display() if customer.title else "",
        "client_number": customer.client_number, "project_number": customer.project_number,
        "contact": customer.contact_en,
    }


def _open_invoices(business_unit, as_of, customer=None, buckets=BUCKETS):
    """Each invoice dated on or before as_of with something still owed on it at the end of that day."""
    cutoff = _end_of(as_of)
    invoices = CustomerInvoice.objects.filter(business_unit=business_unit, issued_at__lt=cutoff).select_related("customer")
    if customer is not None:
        invoices = invoices.filter(customer=customer)
    invoices = list(invoices.order_by("issued_at", "pk"))
    quote_ids = [invoice.quote_id for invoice in invoices]
    paid, credited = {}, {}
    for payment in QuotePayment.objects.filter(quote_id__in=quote_ids, kind__in=OWED_KINDS, created_at__lt=cutoff):
        paid[payment.quote_id] = paid.get(payment.quote_id, ZERO) + payment.amount
    for note in CustomerCreditNote.objects.filter(invoice__in=invoices, issued_at__lt=cutoff):
        credited[note.invoice_id] = credited.get(note.invoice_id, ZERO) + note.applied_to_balance
    for note in AdjustmentNote.objects.filter(invoice__in=invoices, status=AdjustmentNote.Status.APPROVED, decided_at__lt=cutoff):
        credited[note.invoice_id] = credited.get(note.invoice_id, ZERO) + note.amount
    rows = []
    for invoice in invoices:
        owed = invoice.invoice_total - paid.get(invoice.quote_id, ZERO) - credited.get(invoice.pk, ZERO)
        if owed <= 0:
            continue
        age = (as_of - _local_day(invoice.issued_at)).days
        rows.append({
            "invoice": invoice, "owed": owed, "age": age, "bucket": _bucket(age, buckets), "kind": "invoice",
            "paid": paid.get(invoice.quote_id, ZERO), "credited": credited.get(invoice.pk, ZERO),
        })
    from .old_balances import open_balances

    for balance, owed, _in_currency, cleared in open_balances(business_unit, OldBalance.Side.CUSTOMER, as_of, party=customer):
        age = (as_of - balance.document_date).days
        rows.append({
            "invoice": _OldInvoice(balance), "owed": owed, "age": age, "bucket": _bucket(age, buckets), "kind": "old_balance",
            "paid": cleared, "credited": ZERO,
        })
    rows.sort(key=lambda row: row["invoice"].issued_at)
    return rows


class _OldInvoice:
    """An unpaid invoice from the old system, shaped like an ERP invoice for aging and statements."""

    def __init__(self, balance):
        self.pk, self.number, self.quote_id = balance.pk, balance.reference, None
        self.invoice_total, self.customer, self.customer_id = balance.value, balance.party, balance.party_id
        self.issued_at = timezone.make_aware(datetime.datetime.combine(balance.document_date, datetime.time(12)))


def _invoice_row(row):
    invoice = row["invoice"]
    return {
        "id": invoice.pk, "kind": row["kind"], "number": invoice.number, "date": _local_day(invoice.issued_at).isoformat(),
        "quote_id": invoice.quote_id, "total": _money(invoice.invoice_total), "paid": _money(row["paid"]),
        "credited": _money(row["credited"]), "owed": _money(row["owed"]), "age_days": row["age"], "bucket": row["bucket"],
    }


def _empty_buckets(buckets=BUCKETS):
    return {key: ZERO for key, *_ in buckets}


def receivables_aging(*, actor, business_unit, as_of=None, by=None):
    _require_viewer(actor, business_unit)
    as_of = _day(as_of, timezone.localdate(), "as-of")
    buckets = _buckets(by)
    customers, totals = {}, _empty_buckets(buckets)
    for row in _open_invoices(business_unit, as_of, buckets=buckets):
        invoice = row["invoice"]
        entry = customers.setdefault(invoice.customer_id, {
            "customer": invoice.customer, "buckets": _empty_buckets(buckets), "invoices": [], "oldest": 0,
        })
        entry["buckets"][row["bucket"]] += row["owed"]
        totals[row["bucket"]] += row["owed"]
        entry["oldest"] = max(entry["oldest"], row["age"])
        entry["invoices"].append(_invoice_row(row))
    ordered = sorted(customers.values(), key=lambda entry: (-sum(entry["buckets"].values()), entry["customer"].name))
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "as_of": as_of.isoformat(), "by": "years" if buckets is YEAR_BUCKETS else "days",
        "buckets": [{"key": key, "label": label} for key, label, *_ in buckets],
        "customers": [{
            **_customer(entry["customer"]),
            "buckets": {key: _money(value) for key, value in entry["buckets"].items()},
            "total": _money(sum(entry["buckets"].values())), "oldest_days": entry["oldest"],
            "invoices": entry["invoices"],
        } for entry in ordered],
        "totals": {key: _money(value) for key, value in totals.items()},
        "total": _money(sum(totals.values())),
        "policy_label": POLICY_LABEL,
    }


def _movements(business_unit, customer):
    """Every movement of the customer's balance with the unit, oldest first: (moment, order, row, debit, credit)."""
    moves = []
    payments = QuotePayment.objects.filter(
        quote__business_unit=business_unit, quote__customer=customer, kind__in=OWED_KINDS,
    ).select_related("quote")
    for payment in payments:
        label = "Deposit received" if payment.kind == QuotePayment.Kind.DEPOSIT else "Payment received"
        moves.append((payment.created_at, 0, {
            "kind": "payment", "reference": f"Quote {payment.quote_id}",
            "description": f"{label} ({payment.get_method_display().lower()})",
        }, ZERO, payment.amount))
    for invoice in CustomerInvoice.objects.filter(business_unit=business_unit, customer=customer):
        moves.append((invoice.issued_at, 1, {
            "kind": "invoice", "reference": invoice.number, "description": f"Invoice for quote {invoice.quote_id}",
        }, invoice.invoice_total, ZERO))
    for note in CustomerCreditNote.objects.filter(business_unit=business_unit, customer=customer).select_related("invoice"):
        moves.append((note.issued_at, 2, {
            "kind": "credit_note", "reference": note.number, "description": f"Credit note for returned goods on {note.invoice.number}",
        }, ZERO, note.credit_total))
        if note.refunded_amount > 0:
            moves.append((note.issued_at, 3, {
                "kind": "refund", "reference": note.number,
                "description": f"Refund paid by {note.get_refund_method_display().lower()}",
            }, note.refunded_amount, ZERO))
    adjustments = AdjustmentNote.objects.filter(
        business_unit=business_unit, counterparty=customer, kind=AdjustmentNote.Kind.CUSTOMER_CREDIT,
        status=AdjustmentNote.Status.APPROVED,
    ).select_related("invoice")
    for note in adjustments:
        moves.append((note.decided_at, 2, {
            "kind": "credit_note", "reference": note.number,
            "description": f"Credit note on {note.invoice.number}: {note.get_reason_display().lower()}",
        }, ZERO, note.amount))
    for balance in OldBalance.objects.filter(business_unit=business_unit, party=customer, side=OldBalance.Side.CUSTOMER):
        moves.append((_OldInvoice(balance).issued_at, 1, {
            "kind": "old_balance", "reference": balance.reference, "description": "Unpaid invoice brought over from the old system",
        }, balance.value, ZERO))
        for item in balance.settlements.filter(status=OldBalanceSettlement.Status.APPROVED):
            moves.append((timezone.make_aware(datetime.datetime.combine(item.paid_on, datetime.time(12))), 0, {
                "kind": "payment", "reference": balance.reference, "description": "Payment received on an old system invoice",
            }, ZERO, item.cleared_value))
    moves.sort(key=lambda move: (move[0], move[1]))
    return moves


def customer_statement(*, actor, business_unit, customer_id, start=None, end=None, show=None, by=None):
    """show is "all" (every invoice and payment, the default) or "unpaid" (only the invoices still owed at the end date)."""
    _require_viewer(actor, business_unit)
    show = show or "all"
    if show not in ("all", "unpaid"):
        raise InvalidTransaction("Show all transactions or unpaid invoices only.")
    buckets = _buckets(by)
    customer = SyntheticCounterparty.objects.filter(pk=customer_id, party_type=SyntheticCounterparty.Type.CUSTOMER).first()
    has_business = customer is not None and (
        customer.sales_quotes.filter(business_unit=business_unit).exists()
        or customer.customer_invoices.filter(business_unit=business_unit).exists()
        or customer.old_balances.filter(business_unit=business_unit).exists()
    )
    if not has_business:
        raise InvalidTransaction("This customer has no sales with this business unit.")
    end = _day(end, timezone.localdate(), "end")
    start = _day(start, end.replace(month=1, day=1), "start")
    if start > end:
        raise InvalidTransaction("The start date must be on or before the end date.")
    open_invoices = _open_invoices(business_unit, end, customer=customer, buckets=buckets)
    due = _empty_buckets(buckets)
    for row in open_invoices:
        due[row["bucket"]] += row["owed"]
    common = {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "customer": _customer(customer), "show": show, "by": "years" if buckets is YEAR_BUCKETS else "days",
        "start": start.isoformat(), "end": end.isoformat(),
        "aging": {key: _money(value) for key, value in due.items()},
        "invoices_owed": _money(sum(due.values())),
        "buckets": [{"key": key, "label": label} for key, label, *_ in buckets],
        "policy_label": POLICY_LABEL,
    }
    if show == "unpaid":
        running = ZERO
        unpaid = []
        for row in open_invoices:
            running += row["owed"]
            unpaid.append({**_invoice_row(row), "running": _money(running)})
        return {**common, "unpaid": unpaid, "lines": []}
    opening = balance = ZERO
    lines, debits, credits = [], ZERO, ZERO
    for moment, _, row, debit, credit in _movements(business_unit, customer):
        day = _local_day(moment)
        if day > end:
            break
        if day < start:
            opening += debit - credit
            balance = opening
            continue
        balance += debit - credit
        debits += debit
        credits += credit
        lines.append({**row, "date": day.isoformat(), "debit": _money(debit) if debit else "",
                      "credit": _money(credit) if credit else "", "balance": _money(balance)})
    return {
        **common,
        "opening_balance": _money(opening), "closing_balance": _money(balance),
        "total_debits": _money(debits), "total_credits": _money(credits),
        "lines": lines,
    }
