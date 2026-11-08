"""Supplier bills, matched against purchase receipts before the goods can be paid (owner decision).

A person who prepares supplier payments records the supplier's bill for the goods
on a receipt. If the bill amount equals what the receipt booked for the goods, to
the cent and in the receipt's currency, the bill is matched and the goods can be
paid. Any difference holds the bill until a second person, who approves supplier
payments and did not record it, accepts or rejects it.

Accepting a difference posts it at the receipt's rate: a higher bill debits
supplier bill differences and credits receipt clearing, a lower bill the reverse.
The goods are then payable at the bill amount. Shipping, clearance and other
charges are paid as before; their bills are not recorded here yet.

A payable bill can be settled in parts (Bilal, 1 Nov 2026): each payment is
prepared and approved as usual for any amount up to what is still to pay, and
the bill shows what is paid, waiting for approval and still to pay.

A matched or held bill can be voided (with a reason) while no goods payment is
prepared or paid on the receipt, so a wrong bill can be replaced. Accounts and
rules are provisional.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from .models import BASE_CURRENCY, JournalEntry, PurchaseReceipt, SupplierBill, SupplierPayment
from .purchasing_services import WorkflowPermissionDenied, _to_base
from .supplier_payments import (
    TEST_ONLY_LABEL,
    _date,
    _locked_receipt,
    _payable,
    _received,
    _require_approver,
    _require_user,
    _text,
    can_approve,
    can_pay,
)
from .transaction_services import InvalidTransaction, _begin_idempotent_operation, _complete, _money, _post_journal

ZERO = Decimal("0.00")
POLICY_LABEL = (
    "TEST ONLY: goods are paid only against a supplier bill that matches the receipt to the cent, "
    "or whose difference a second person accepted. Accounts are provisional."
)


def _require_recorder(user, business_unit):
    _require_user(user, business_unit)
    if not can_pay(user, business_unit):
        raise WorkflowPermissionDenied("You need permission to prepare supplier payments to record supplier bills.")


def _amount(value):
    return None if value is None else format(value, "f")


def _goods_committed(receipt):
    return sum(
        SupplierPayment.objects.filter(
            receipt=receipt, charge__isnull=True,
            status__in=(SupplierPayment.Status.REQUESTED, SupplierPayment.Status.APPROVED),
        ).values_list("amount", flat=True),
        ZERO,
    )


@transaction.atomic
def record_bill(*, actor, idempotency_key, receipt_id, bill_number, bill_date, amount, note=""):
    """Record the supplier's bill for the goods on a receipt and match it against what was received."""
    receipt = _locked_receipt(receipt_id)
    _require_recorder(actor, receipt.business_unit)
    number = _text(bill_number, "bill number", 40)
    if not number:
        raise InvalidTransaction("Give the supplier's bill number.")
    if bill_date in (None, ""):
        raise InvalidTransaction("Give the date on the supplier's bill.")
    day = _date(bill_date, "bill date")
    amount = _money(amount, "bill amount")
    if amount <= 0:
        raise InvalidTransaction("The bill amount must be greater than zero.")
    note = _text(note, "note", 240)
    payload = {"receipt_id": receipt.pk, "bill_number": number, "bill_date": day.isoformat(),
               "amount": amount, "note": note}
    operation, created = _begin_idempotent_operation(idempotency_key, "supplier_bill", payload)
    if not created:
        return operation.result
    current = receipt.supplier_bills.filter(status__in=SupplierBill.ACTIVE).first()
    if current is not None:
        raise InvalidTransaction(
            f"Receipt {receipt.pk} already has bill {current.bill_number} ({current.get_status_display().lower()}). "
            "Void or reject it before recording another."
        )
    duplicate = SupplierBill.objects.filter(
        supplier=receipt.supplier, status__in=SupplierBill.ACTIVE,
    ).annotate(lowered=Lower("bill_number")).filter(lowered=number.lower()).first()
    if duplicate is not None:
        raise InvalidTransaction(
            f"Bill {duplicate.bill_number} from {receipt.supplier.name} is already recorded on receipt {duplicate.receipt_id}."
        )
    expected = _received(receipt).payable
    committed = _goods_committed(receipt)
    if amount < committed:
        raise InvalidTransaction(
            f"{format(committed, 'f')} {receipt.currency} is already paid or waiting for approval on these goods, "
            "more than this bill."
        )
    difference = amount - expected
    bill = SupplierBill.objects.create(
        idempotency=operation, receipt=receipt, business_unit=receipt.business_unit, supplier=receipt.supplier,
        bill_number=number, bill_date=day, currency=receipt.currency, amount=amount, expected_amount=expected,
        difference=difference, status=SupplierBill.Status.MATCHED if difference == 0 else SupplierBill.Status.ON_HOLD,
        recorded_by=actor, note=note,
    )
    return _complete(operation, bill_row(bill))


def _locked_bill(bill_id):
    if type(bill_id) is not int:
        raise InvalidTransaction("Choose the supplier bill.")
    bill = SupplierBill.objects.select_for_update().filter(pk=bill_id, is_synthetic=True).first()
    if bill is None:
        raise InvalidTransaction("Supplier bill not found.")
    return bill


def _second_person(actor, bill, verb):
    _require_approver(actor, bill.business_unit)
    if bill.recorded_by_id == actor.pk:
        raise WorkflowPermissionDenied(f"You recorded this bill, so someone else must {verb} the difference.")
    if bill.status != SupplierBill.Status.ON_HOLD:
        raise InvalidTransaction(f"This bill is {bill.get_status_display().lower()}, so there is no difference to decide.")


@transaction.atomic
def accept_bill(*, actor, idempotency_key, bill_id, note=""):
    """A second person accepts the difference; it is posted and the goods become payable at the bill amount."""
    bill = _locked_bill(bill_id)
    _require_approver(actor, bill.business_unit)
    operation, created = _begin_idempotent_operation(idempotency_key, "supplier_bill_acceptance", {"bill_id": bill.pk})
    if not created:
        return operation.result
    _second_person(actor, bill, "accept")
    receipt = _locked_receipt(bill.receipt_id)
    note = _text(note, "note", 240)
    size = _to_base(abs(bill.difference), receipt.exchange_rate)
    value = size if bill.difference > 0 else -size
    entry = None
    if size > 0:
        higher = bill.difference > 0
        what = f"bill {bill.bill_number} for receipt {receipt.pk}"
        lines = [
            ("TEST_BILL_DIFFERENCES", size, ZERO, f"Bill higher than goods received: {what}"),
            ("TEST_GRNI", ZERO, size, f"Receipt clearing raised to the bill: {what}"),
        ] if higher else [
            ("TEST_GRNI", size, ZERO, f"Receipt clearing lowered to the bill: {what}"),
            ("TEST_BILL_DIFFERENCES", ZERO, size, f"Bill lower than goods received: {what}"),
        ]
        entry = _post_journal(
            legal_entity=receipt.business_unit.legal_entity, business_unit=receipt.business_unit, idempotency=operation,
            kind=JournalEntry.Kind.SUPPLIER_BILL,
            description=f"{TEST_ONLY_LABEL}: accepted difference on {what} from {receipt.supplier.name}",
            lines=lines, document_date=bill.bill_date,
        )
    bill.status = SupplierBill.Status.ACCEPTED
    bill.acceptance = operation
    bill.journal_entry = entry
    bill.difference_value = value
    bill.decided_by = actor
    bill.decided_at = timezone.now()
    bill.decision_note = note
    bill.save()
    return _complete(operation, bill_row(bill))


@transaction.atomic
def reject_bill(*, actor, bill_id, note):
    bill = _locked_bill(bill_id)
    _second_person(actor, bill, "reject")
    note = _text(note, "reason", 240)
    if not note:
        raise InvalidTransaction("Say why the bill is rejected, for example what the supplier must correct.")
    bill.status = SupplierBill.Status.REJECTED
    bill.decided_by = actor
    bill.decided_at = timezone.now()
    bill.decision_note = note
    bill.save()
    return bill_row(bill)


@transaction.atomic
def void_bill(*, actor, bill_id, note):
    """Set aside a matched or held bill recorded in error, while nothing has been paid against it."""
    bill = _locked_bill(bill_id)
    _require_user(actor, bill.business_unit)
    if not (can_pay(actor, bill.business_unit) or can_approve(actor, bill.business_unit)):
        raise WorkflowPermissionDenied("You need permission to prepare or approve supplier payments to void a bill.")
    if bill.status not in (SupplierBill.Status.MATCHED, SupplierBill.Status.ON_HOLD):
        raise InvalidTransaction(f"This bill is {bill.get_status_display().lower()} and cannot be voided.")
    if _goods_committed(bill.receipt):
        raise InvalidTransaction("A goods payment is prepared or paid against this bill, so it cannot be voided.")
    note = _text(note, "reason", 240)
    if not note:
        raise InvalidTransaction("Say why the bill is voided.")
    bill.status = SupplierBill.Status.VOIDED
    bill.decided_by = actor
    bill.decided_at = timezone.now()
    bill.decision_note = note
    bill.save()
    return bill_row(bill)


def _lines(receipt):
    foreign = receipt.currency != BASE_CURRENCY
    rows = []
    for line in receipt.lines.select_related("variant", "commitment_line__commitment").order_by("pk"):
        ordered = line.commitment_line
        rows.append({
            "sku": line.variant.sku, "description": line.variant.description,
            "received_quantity": format(line.quantity, "f"),
            "unit_cost": format(line.foreign_unit_cost if foreign else line.unit_cost, "f"),
            "value": format(line.foreign_purchase_value if foreign else line.purchase_value, "f"),
            "ordered_quantity": format(ordered.ordered_quantity, "f") if ordered else None,
            "ordered_unit_cost": format(ordered.unit_cost, "f") if ordered else None,
            "ordered_currency": ordered.commitment.currency if ordered else None,
        })
    return rows


def _settlement(bill):
    """What is paid, waiting for approval and still to pay on a matched or accepted bill."""
    if bill.status not in SupplierBill.PAYABLE:
        return {"settlement": "", "settlement_label": "", "paid": None, "waiting": None, "outstanding": None}
    item = _payable(bill.receipt)
    paid, committed = item.paid(), item.committed()
    outstanding = max(item.payable - committed, Decimal("0"))
    if outstanding == 0 and committed == paid:
        state = ("paid", "Paid in full")
    elif paid > 0:
        state = ("part_paid", "Part paid")
    elif committed > 0:
        state = ("waiting", "Payment waiting for approval")
    else:
        state = ("unpaid", "Not paid yet")
    return {"settlement": state[0], "settlement_label": state[1], "paid": format(paid, "f"),
            "waiting": format(committed - paid, "f"), "outstanding": format(outstanding, "f")}


def bill_row(bill):
    return _settlement(bill) | {
        "id": bill.pk,
        "receipt_id": bill.receipt_id,
        "supplier": {"id": bill.supplier_id, "code": bill.supplier.code, "name": bill.supplier.name},
        "bill_number": bill.bill_number,
        "bill_date": bill.bill_date.isoformat(),
        "currency": bill.currency,
        "amount": format(bill.amount, "f"),
        "expected_amount": format(bill.expected_amount, "f"),
        "difference": format(bill.difference, "f"),
        "status": bill.status,
        "status_label": bill.get_status_display(),
        "note": bill.note,
        "recorded_by": bill.recorded_by.username,
        "decided_by": bill.decided_by.username if bill.decided_by_id else None,
        "decision_note": bill.decision_note,
        "difference_value": _amount(bill.difference_value),
        "journal_entry_id": bill.journal_entry_id,
        "received_on": timezone.localtime(bill.receipt.created_at).date().isoformat(),
        "can_void": bill.status in (SupplierBill.Status.MATCHED, SupplierBill.Status.ON_HOLD) and not _goods_committed(bill.receipt),
    }


def bills_overview(*, actor, business_unit):
    _require_user(actor, business_unit)
    recorder, decider = can_pay(actor, business_unit), can_approve(actor, business_unit)
    if not (recorder or decider):
        raise WorkflowPermissionDenied("You need permission to prepare or approve supplier payments in this business unit.")
    bills = SupplierBill.objects.filter(business_unit=business_unit, is_synthetic=True).select_related(
        "supplier", "receipt", "recorded_by", "decided_by",
    )
    with_bill = bills.filter(status__in=SupplierBill.ACTIVE).values_list("receipt_id", flat=True)
    receipts = (
        PurchaseReceipt.objects.filter(business_unit=business_unit, is_synthetic=True)
        .exclude(pk__in=with_bill).select_related("supplier").order_by("created_at", "pk")
    )
    needing = []
    for receipt in receipts:
        goods = _payable(receipt)
        if goods.payable - _goods_committed(receipt) <= 0:
            continue
        needing.append({
            "id": receipt.pk,
            "supplier": {"id": receipt.supplier_id, "code": receipt.supplier.code, "name": receipt.supplier.name},
            "received_on": timezone.localtime(receipt.created_at).date().isoformat(),
            "currency": receipt.currency,
            "expected_amount": format(goods.payable, "f"),
            "lines": _lines(receipt),
        })
        if len(needing) == 100:
            break
    waiting = [bill_row(bill) | {"lines": _lines(bill.receipt)} for bill in bills.filter(status=SupplierBill.Status.ON_HOLD)[:100]]
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "can_record": recorder,
        "can_decide": decider,
        "me": actor.username,
        "needing_bill": needing,
        "waiting": waiting,
        "bills": [bill_row(bill) for bill in bills.filter(~Q(status=SupplierBill.Status.ON_HOLD))[:50]],
        "policy_label": POLICY_LABEL,
    }
