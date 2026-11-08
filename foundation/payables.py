"""What the business unit owes: unpaid purchase receipts and their charges, by age.

Read-only. Each receipt can owe the supplier for the goods and other parties for
its eligible shipping, clearance and other charges. What is still owed on each
is the QAR the receipt booked for it, less what approved payments dated on or
before the chosen day cleared. For a foreign-currency item the amount still owed
in that currency is shown too. Payments waiting for approval do not reduce what
is owed. The goods are aged from the supplier's bill date once a bill is
recorded (and owed at the bill amount once a difference is accepted); charges,
and goods without a bill, are aged from the receipt date.

Visible to anyone who can view the general ledger, prepare supplier payments or
approve them in the business unit.
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .general_ledger import _day, can_view_ledger
from .models import BASE_CURRENCY, DemoUserMarker, OldBalance, OldBalanceSettlement, PurchaseReceipt, SupplierPayment
from .purchasing_services import WorkflowPermissionDenied
from .receivables import YEAR_BUCKETS, _bucket, _buckets, _empty_buckets, _end_of, _local_day, _money
from .supplier_payments import _bill_status, _payable, _received, can_approve, can_pay
from .transaction_services import _require_demo

ZERO = Decimal("0.00")
POLICY_LABEL = (
    "TEST ONLY: goods are aged from the supplier's bill date once recorded, everything else from the receipt date. "
    "Amounts are in QAR at the rate each receipt was booked at; payments waiting for approval are not deducted."
)


def can_view_payables(user, business_unit):
    return can_view_ledger(user, business_unit) or can_pay(user, business_unit) or can_approve(user, business_unit)


def _require_viewer(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Payables run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can view payables.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not can_view_payables(user, business_unit):
        raise WorkflowPermissionDenied(
            "You need permission to view the general ledger or to prepare or approve supplier payments in this business unit."
        )


def _settled(receipt, charge, cutoff_day):
    """Approved payments on or before the day: QAR cleared and amount paid in the item's currency."""
    approved = SupplierPayment.objects.filter(
        receipt=receipt, charge=charge, status=SupplierPayment.Status.APPROVED, paid_on__lte=cutoff_day,
    ).aggregate(cleared=Sum("cleared_value"), paid=Sum("amount"))
    return approved["cleared"] or ZERO, approved["paid"] or ZERO


def _waiting(receipt, charge):
    return SupplierPayment.objects.filter(
        receipt=receipt, charge=charge, status=SupplierPayment.Status.REQUESTED,
    ).aggregate(total=Sum("paid_value"))["total"] or ZERO


def payables_aging(*, actor, business_unit, as_of=None, by=None):
    _require_viewer(actor, business_unit)
    as_of = _day(as_of, timezone.localdate(), "as-of")
    buckets = _buckets(by)
    receipts = (
        PurchaseReceipt.objects.filter(business_unit=business_unit, is_synthetic=True, created_at__lt=_end_of(as_of))
        .select_related("supplier").prefetch_related("charges").order_by("created_at", "pk")
    )
    suppliers, totals, waiting_total = {}, _empty_buckets(buckets), ZERO
    for receipt in receipts:
        received = _local_day(receipt.created_at)
        goods = _payable(receipt)
        if goods.bill is not None and goods.bill.decided_at and _local_day(goods.bill.decided_at) > as_of:
            goods = _received(receipt)  # the difference was accepted after the chosen day
        items = [goods] + [_payable(receipt, charge) for charge in receipt.charges.all() if charge.is_eligible]
        for item in items:
            bill = _bill_status(item)
            if bill and bill["date"] and bill["date"] > as_of.isoformat():
                bill = {"id": None, "number": "", "status": "missing", "label": "No bill recorded yet", "date": None,
                        "payable": False}
            since = datetime.date.fromisoformat(bill["date"]) if bill and bill["date"] else received
            age = max((as_of - since).days, 0)
            bucket = _bucket(age, buckets)
            cleared, paid = _settled(receipt, item.charge, as_of)
            owed = item.carried - cleared
            if owed <= 0:
                continue
            waiting = _waiting(receipt, item.charge)
            entry = suppliers.setdefault(receipt.supplier_id, {
                "supplier": receipt.supplier, "buckets": _empty_buckets(buckets), "items": [], "oldest": 0, "waiting": ZERO,
            })
            entry["buckets"][bucket] += owed
            entry["oldest"] = max(entry["oldest"], age)
            entry["waiting"] += waiting
            totals[bucket] += owed
            waiting_total += waiting
            foreign = item.currency != BASE_CURRENCY
            entry["items"].append({
                "receipt_id": receipt.pk, "received_on": received.isoformat(), "age_days": age, "bucket": bucket,
                "bill": bill,
                "paying_for": "goods" if item.charge is None else item.charge.category,
                "label": "Goods" if item.charge is None else item.charge.get_category_display(),
                "currency": item.currency, "booked": _money(item.carried), "cleared": _money(cleared), "owed": _money(owed),
                "owed_in_currency": _money(item.payable - paid) if foreign else None,
                "waiting_approval": _money(waiting),
            })
    from .old_balances import open_balances

    for balance, owed, owed_in_currency, cleared in open_balances(business_unit, OldBalance.Side.SUPPLIER, as_of):
        age = max((as_of - balance.document_date).days, 0)
        bucket = _bucket(age, buckets)
        waiting = balance.settlements.filter(status=OldBalanceSettlement.Status.REQUESTED).aggregate(total=Sum("paid_value"))["total"] or ZERO
        entry = suppliers.setdefault(balance.party_id, {
            "supplier": balance.party, "buckets": _empty_buckets(buckets), "items": [], "oldest": 0, "waiting": ZERO,
        })
        entry["buckets"][bucket] += owed
        entry["oldest"] = max(entry["oldest"], age)
        entry["waiting"] += waiting
        totals[bucket] += owed
        waiting_total += waiting
        entry["items"].append({
            "receipt_id": None, "old_balance_id": balance.pk, "received_on": balance.document_date.isoformat(), "age_days": age,
            "bucket": bucket, "bill": {"id": None, "number": balance.reference, "status": "old", "label": "Bill from the old system",
                                       "date": balance.document_date.isoformat(), "payable": True},
            "paying_for": "old_balance", "label": "Old system bill", "currency": balance.currency,
            "booked": _money(balance.value), "cleared": _money(cleared), "owed": _money(owed),
            "owed_in_currency": _money(owed_in_currency) if balance.currency != BASE_CURRENCY else None,
            "waiting_approval": _money(waiting),
        })
    ordered = sorted(suppliers.values(), key=lambda entry: (-sum(entry["buckets"].values()), entry["supplier"].name))
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "as_of": as_of.isoformat(), "by": "years" if buckets is YEAR_BUCKETS else "days",
        "buckets": [{"key": key, "label": label} for key, label, *_ in buckets],
        "suppliers": [{
            "id": entry["supplier"].pk, "code": entry["supplier"].code, "name": entry["supplier"].name,
            "buckets": {key: _money(value) for key, value in entry["buckets"].items()},
            "total": _money(sum(entry["buckets"].values())), "oldest_days": entry["oldest"],
            "waiting_approval": _money(entry["waiting"]), "items": entry["items"],
        } for entry in ordered],
        "totals": {key: _money(value) for key, value in totals.items()},
        "total": _money(sum(totals.values())),
        "waiting_approval": _money(waiting_total),
        "can_pay": can_pay(actor, business_unit),
        "currency": BASE_CURRENCY,
        "policy_label": POLICY_LABEL,
    }
