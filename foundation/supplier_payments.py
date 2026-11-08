"""Payments against purchase receipts, with maker/checker approval and realized exchange gain or loss.

Each receipt can be paid in parts: to the supplier for the goods, and to other
parties (freight forwarders, customs brokers, and so on) for each shipping,
clearance or other charge recorded on the receipt.

One person prepares a payment (pay_supplier permission) and a different person
approves it (approve_payment permission) before anything is posted. Approval
posts the journal, dated on the payment date:

- goods: Dr receipt clearing at the receipt rate, Cr bank at the payment rate;
- a charge: Dr landed-charge clearing at the charge's rate, Cr bank at the payment rate;
- the difference in both cases is a realized exchange gain or loss (not split by currency).

The final payment of an item clears whatever QAR it still carries, so rounding on
earlier part-payments never leaves a few dirhams behind. Accounts are provisional.

The goods can be paid only once the supplier's bill is recorded and matches the
receipt, or a second person accepted its difference (see supplier_bills). An
accepted bill makes the goods payable at the bill amount.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .approval_limits import require_within_limit
from .access import active_grants
from .models import (
    AdjustmentNote,
    BASE_CURRENCY,
    SupplierAdvance,
    DemoUserMarker,
    JournalEntry,
    PurchaseReceipt,
    PurchaseReceiptCharge,
    SupplierBill,
    SupplierPayment,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied, _exchange_terms, _to_base, require_supplier_not_frozen
from .transaction_services import (
    InvalidTransaction,
    _begin_idempotent_operation,
    _complete,
    _money,
    _post_journal,
    _require_demo,
)

ZERO = Decimal("0.00")
TEST_ONLY_LABEL = "TEST ONLY"
PREPARE = UserActionGrant.Action.PAY_SUPPLIER
APPROVE = UserActionGrant.Action.APPROVE_PAYMENT
OPEN = (SupplierPayment.Status.REQUESTED, SupplierPayment.Status.APPROVED)


def can_pay(user, business_unit):
    return active_grants(user, PREPARE).filter(business_unit=business_unit).exists()


def can_approve(user, business_unit):
    return active_grants(user, APPROVE).filter(business_unit=business_unit).exists()


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Supplier payments run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can work with supplier payments.")
    _require_demo(business_unit, business_unit.legal_entity)


def _require_payer(user, business_unit):
    _require_user(user, business_unit)
    if not can_pay(user, business_unit):
        raise WorkflowPermissionDenied("You do not have permission to prepare supplier payments in this business unit.")


def _require_approver(user, business_unit):
    _require_user(user, business_unit)
    if not can_approve(user, business_unit):
        raise WorkflowPermissionDenied("You do not have permission to approve supplier payments in this business unit.")


@dataclass
class Payable:
    """One thing on a receipt that can be paid: the supplier's goods or one charge."""

    receipt: PurchaseReceipt
    charge: PurchaseReceiptCharge | None
    currency: str
    payable: Decimal  # in currency
    carried: Decimal  # QAR the receipt booked for it
    rate: Decimal  # QAR per unit on the receipt (or charge) day
    account: str
    bill: SupplierBill | None = None  # goods only: the matched or accepted supplier bill

    @property
    def label(self):
        if self.charge is None:
            return f"Goods from {self.receipt.supplier.name}"
        return self.charge.get_category_display()

    def payments(self, statuses=OPEN):
        return SupplierPayment.objects.filter(receipt=self.receipt, charge=self.charge, status__in=statuses)

    def committed(self):
        """Amount already paid or waiting for approval."""
        return self.payments().aggregate(total=Sum("amount"))["total"] or ZERO

    def cleared(self):
        return self.payments((SupplierPayment.Status.APPROVED,)).aggregate(total=Sum("cleared_value"))["total"] or ZERO

    def paid(self):
        return self.payments((SupplierPayment.Status.APPROVED,)).aggregate(total=Sum("amount"))["total"] or ZERO


def _received(receipt):
    """The goods as the receipt booked them, before any supplier bill."""
    foreign = receipt.currency != BASE_CURRENCY
    return Payable(
        receipt, None, receipt.currency, receipt.foreign_purchase_value if foreign else receipt.purchase_value,
        receipt.purchase_value, receipt.exchange_rate, "TEST_GRNI",
    )


def _payable(receipt, charge=None):
    if charge is None:
        item = _received(receipt)
        item.bill = receipt.supplier_bills.filter(status__in=SupplierBill.PAYABLE).first()
        if item.bill is not None and item.bill.status == SupplierBill.Status.ACCEPTED:
            # An accepted difference makes the goods payable at the bill amount and moved receipt clearing with it.
            item.payable = item.bill.amount
            item.carried += item.bill.difference_value
        # Approved supplier credit and debit notes lower or raise what is owed for the goods.
        for note in receipt.adjustment_notes.filter(status=AdjustmentNote.Status.APPROVED):
            sign = -1 if note.kind in AdjustmentNote.LOWERS else 1
            item.payable += sign * note.amount
            item.carried += sign * note.value
        return item
    foreign = charge.currency != BASE_CURRENCY
    return Payable(
        receipt, charge, charge.currency, charge.foreign_amount if foreign else charge.amount,
        charge.amount, charge.exchange_rate, "TEST_LANDED_CLEARING",
    )


def _date(value, field):
    if value in (None, ""):
        return timezone.localdate()
    try:
        day = value if isinstance(value, datetime.date) else datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise InvalidTransaction(f"Give the {field} as YYYY-MM-DD.") from None
    if day > timezone.localdate():
        raise InvalidTransaction(f"The {field} cannot be in the future.")
    return day


def _text(value, field, limit, required=False):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if len(text) > limit:
        raise InvalidTransaction(f"The {field} can be at most {limit} characters.")
    if required and not text:
        raise InvalidTransaction(f"Say who is being paid ({field}).")
    return text


def _require_bill(receipt):
    """Goods are paid only against a matched or accepted supplier bill (owner decision)."""
    held = receipt.supplier_bills.filter(status=SupplierBill.Status.ON_HOLD).first()
    if held is not None:
        raise InvalidTransaction(
            f"Bill {held.bill_number} differs from receipt {receipt.pk} by {format(held.difference, 'f')} {held.currency}. "
            "A second person must accept the difference before the goods can be paid."
        )
    raise InvalidTransaction(f"Record the supplier's bill for receipt {receipt.pk} before paying for the goods.")


def _locked_receipt(receipt_id):
    if type(receipt_id) is not int:
        raise InvalidTransaction("Choose the purchase receipt to pay.")
    receipt = (
        PurchaseReceipt.objects.select_for_update(of=("self",))
        .select_related("business_unit__legal_entity", "supplier")
        .filter(pk=receipt_id, is_synthetic=True)
        .first()
    )
    if receipt is None:
        raise InvalidTransaction("Purchase receipt not found.")
    return receipt


def advance_left(advance):
    """What is still unused on an advance: its amount less uses paid or waiting for approval."""
    used = advance.applications.filter(status__in=OPEN).aggregate(total=Sum("amount"))["total"] or ZERO
    return advance.amount - used


def _usable_advance(advance_id, receipt, charge, item, amount):
    if type(advance_id) is not int:
        raise InvalidTransaction("Choose an advance paid to this supplier.")
    advance = SupplierAdvance.objects.select_for_update().filter(pk=advance_id, business_unit=receipt.business_unit).first()
    if advance is None or advance.supplier_id != receipt.supplier_id:
        raise InvalidTransaction(f"Choose an advance paid to {receipt.supplier.name}.")
    if advance.status != SupplierAdvance.Status.APPROVED:
        raise InvalidTransaction(f"Advance {advance.number} is not approved and paid yet.")
    if charge is not None:
        raise InvalidTransaction("An advance to the supplier can be set only against the supplier's goods.")
    if advance.currency != item.currency:
        raise InvalidTransaction(f"Advance {advance.number} is in {advance.currency} but receipt {receipt.pk} is in {item.currency}.")
    left = advance_left(advance)
    if amount > left:
        raise InvalidTransaction(f"Advance {advance.number} has only {format(left, 'f')} {advance.currency} left to use.")
    return advance


@transaction.atomic
def request_payment(
    *, actor, idempotency_key, receipt_id, amount, charge_id=None, payee_name="", exchange_rate=None,
    rate_source="", rate_date=None, paid_on=None, bank_reference="", advance_id=None,
):
    """Prepare a payment. Nothing is posted until someone else approves it.

    With advance_id the money comes from an approved advance already paid to the
    supplier: goods only, in the advance's currency and at its rate.
    """
    receipt = _locked_receipt(receipt_id)
    _require_payer(actor, receipt.business_unit)
    require_supplier_not_frozen(receipt.supplier)
    charge = None
    if charge_id not in (None, ""):
        if type(charge_id) is not int:
            raise InvalidTransaction("Choose a charge on this receipt.")
        charge = receipt.charges.filter(pk=charge_id, is_eligible=True).first()
        if charge is None:
            raise InvalidTransaction("Choose a charge on this receipt.")
    item = _payable(receipt, charge)
    if charge is None and item.bill is None:
        _require_bill(receipt)
    amount = _money(amount, "payment amount")
    if amount <= 0:
        raise InvalidTransaction("The payment amount must be greater than zero.")
    advance = None
    if advance_id not in (None, ""):
        advance = _usable_advance(advance_id, receipt, charge, item, amount)
        exchange_rate, rate_source, rate_date = advance.exchange_rate, advance.rate_source, advance.rate_date
        bank_reference = f"From advance {advance.number}"
    terms = _exchange_terms(item.currency, exchange_rate, rate_source, rate_date)
    paid_on = _date(paid_on, "payment date")
    if advance is not None and paid_on < advance.paid_on:
        raise InvalidTransaction(f"Advance {advance.number} cannot be used before the day it was paid.")
    payee = receipt.supplier.name if charge is None else _text(payee_name, "payee", 120, required=True)
    reference = _text(bank_reference, "bank reference", 80)
    payload = {
        "receipt_id": receipt.pk, "charge_id": charge.pk if charge else None, "payee": payee, "amount": amount,
        "exchange_rate": terms["exchange_rate"], "rate_source": terms["rate_source"],
        "rate_date": terms["rate_date"].isoformat() if terms["rate_date"] else None,
        "paid_on": paid_on.isoformat(), "bank_reference": reference, "advance_id": advance.pk if advance else None,
    }
    operation, created = _begin_idempotent_operation(idempotency_key, "supplier_payment", payload)
    if not created:
        return operation.result
    remaining = item.payable - item.committed()
    if remaining <= 0:
        raise InvalidTransaction(f"{item.label} is already paid in full or waiting for approval.")
    if amount > remaining:
        raise InvalidTransaction(
            f"The payment is more than the {format(remaining, 'f')} {item.currency} still to pay for {item.label.lower()}."
        )
    payment = SupplierPayment.objects.create(
        idempotency=operation, receipt=receipt, charge=charge, business_unit=receipt.business_unit,
        supplier=receipt.supplier, payee_name=payee, currency=terms["currency"], amount=amount,
        exchange_rate=terms["exchange_rate"], rate_source=terms["rate_source"], rate_date=terms["rate_date"],
        paid_value=_to_base(amount, terms["exchange_rate"]), paid_on=paid_on, bank_reference=reference, paid_by=actor,
        advance=advance,
    )
    return _complete(operation, _payment_row(payment))


def _locked_request(actor, payment_id):
    if type(payment_id) is not int:
        raise InvalidTransaction("Choose the payment.")
    payment = SupplierPayment.objects.select_for_update().filter(pk=payment_id, is_synthetic=True).first()
    if payment is None:
        raise InvalidTransaction("Payment not found.")
    _require_approver(actor, payment.business_unit)
    return payment


@transaction.atomic
def approve_payment(*, actor, idempotency_key, payment_id):
    """Approve a prepared payment and post its journal. The approver must not be the person who prepared it."""
    payment = _locked_request(actor, payment_id)
    operation, created = _begin_idempotent_operation(idempotency_key, "supplier_payment_approval", {"payment_id": payment.pk})
    if not created:
        return operation.result
    if payment.status != SupplierPayment.Status.REQUESTED:
        raise InvalidTransaction(f"This payment is already {payment.get_status_display().lower()}.")
    if payment.paid_by_id == actor.pk:
        raise WorkflowPermissionDenied("You prepared this payment, so someone else must approve it.")
    require_within_limit(actor, payment.business_unit, APPROVE, payment.paid_value, "this payment")
    receipt = _locked_receipt(payment.receipt_id)
    item = _payable(receipt, payment.charge)
    paid_before = item.paid()
    # The last payment clears what the item still carries; earlier ones clear at the receipt (or charge) rate.
    if paid_before + payment.amount == item.payable:
        cleared = item.carried - item.cleared()
    else:
        cleared = _to_base(payment.amount, item.rate)
    difference = cleared - payment.paid_value
    lines = [
        (item.account, cleared, ZERO, f"{item.label} cleared at the receipt rate (receipt {receipt.pk})"),
        ("TEST_SUPPLIER_ADVANCES", ZERO, payment.paid_value, f"Advance {payment.advance.number} used")
        if payment.advance_id else ("TEST_CASH", ZERO, payment.paid_value, f"Paid to {payment.payee_name}"),
    ]
    if difference > 0:
        lines.append(("TEST_FX_GAIN", ZERO, difference, "Realized exchange gain on payment"))
    elif difference < 0:
        lines.append(("TEST_FX_LOSS", -difference, ZERO, "Realized exchange loss on payment"))
    what = "goods" if payment.charge is None else payment.charge.get_category_display().lower()
    entry = _post_journal(
        legal_entity=receipt.business_unit.legal_entity,
        business_unit=receipt.business_unit,
        idempotency=operation,
        kind=JournalEntry.Kind.SUPPLIER_PAYMENT,
        description=f"{TEST_ONLY_LABEL}: payment to {payment.payee_name} for {what} on receipt {receipt.pk}",
        lines=lines,
        document_date=payment.paid_on,
    )
    payment.status = SupplierPayment.Status.APPROVED
    payment.approval = operation
    payment.journal_entry = entry
    payment.cleared_value = cleared
    payment.exchange_difference = difference
    payment.decided_by = actor
    payment.decided_at = timezone.now()
    payment.save()
    outstanding = item.payable - paid_before - payment.amount
    return _complete(operation, _payment_row(payment) | {"outstanding": format(outstanding, "f")})


@transaction.atomic
def reject_payment(*, actor, payment_id, note):
    payment = _locked_request(actor, payment_id)
    if payment.status != SupplierPayment.Status.REQUESTED:
        raise InvalidTransaction(f"This payment is already {payment.get_status_display().lower()}.")
    if payment.paid_by_id == actor.pk:
        raise WorkflowPermissionDenied("You prepared this payment, so someone else must decide on it.")
    note = _text(note, "reason", 240)
    if not note:
        raise InvalidTransaction("A reason for rejecting the payment is required.")
    payment.status = SupplierPayment.Status.REJECTED
    payment.decided_by = actor
    payment.decided_at = timezone.now()
    payment.decision_note = note
    payment.save()
    return _payment_row(payment)


def _amount(value):
    return None if value is None else format(value, "f")


def _payment_row(payment):
    charge = payment.charge
    return {
        "id": payment.pk,
        "receipt_id": payment.receipt_id,
        "status": payment.status,
        "status_label": payment.get_status_display(),
        "supplier": {"id": payment.supplier_id, "code": payment.supplier.code, "name": payment.supplier.name},
        "paying_for": "goods" if charge is None else charge.category,
        "paying_for_label": "Goods" if charge is None else charge.get_category_display(),
        "charge_id": charge.pk if charge else None,
        "payee_name": payment.payee_name,
        "currency": payment.currency,
        "amount": format(payment.amount, "f"),
        "exchange_rate": format(payment.exchange_rate, "f"),
        "rate_source": payment.rate_source,
        "rate_date": payment.rate_date.isoformat() if payment.rate_date else None,
        "paid_value": format(payment.paid_value, "f"),
        "cleared_value": _amount(payment.cleared_value),
        "exchange_difference": _amount(payment.exchange_difference),
        "paid_on": payment.paid_on.isoformat(),
        "bank_reference": payment.bank_reference,
        "paid_by": payment.paid_by.username,
        "decided_by": payment.decided_by.username if payment.decided_by_id else None,
        "decision_note": payment.decision_note,
        "journal_entry_id": payment.journal_entry_id,
        "advance": payment.advance.number if payment.advance_id else None,
    }


def _bill_status(item):
    """Goods only: whether the supplier bill allows paying them."""
    if item.charge is not None:
        return None
    if item.bill is not None:
        return {"id": item.bill.pk, "number": item.bill.bill_number, "status": item.bill.status,
                "label": item.bill.get_status_display(), "date": item.bill.bill_date.isoformat(), "payable": True}
    held = item.receipt.supplier_bills.filter(status=SupplierBill.Status.ON_HOLD).first()
    if held is not None:
        return {"id": held.pk, "number": held.bill_number, "status": held.status,
                "label": held.get_status_display(), "date": held.bill_date.isoformat(), "payable": False}
    return {"id": None, "number": "", "status": "missing", "label": "No bill recorded yet", "date": None, "payable": False}


def _item_row(item):
    committed, paid = item.committed(), item.paid()
    return {
        "bill": _bill_status(item),
        "charge_id": item.charge.pk if item.charge else None,
        "paying_for": "goods" if item.charge is None else item.charge.category,
        "label": item.label,
        "currency": item.currency,
        "exchange_rate": format(item.rate, "f"),
        "payable": format(item.payable, "f"),
        "paid": format(paid, "f"),
        "waiting": format(committed - paid, "f"),
        "remaining": format(item.payable - committed, "f"),
        "carried_value": format(item.carried, "f"),
    }


def payments_overview(*, actor, business_unit):
    _require_user(actor, business_unit)
    preparer, approver = can_pay(actor, business_unit), can_approve(actor, business_unit)
    if not (preparer or approver):
        raise WorkflowPermissionDenied("You do not have permission to prepare or approve supplier payments in this business unit.")
    receipts = (
        PurchaseReceipt.objects.filter(business_unit=business_unit, is_synthetic=True)
        .select_related("supplier")
        .prefetch_related("charges")
        .order_by("-created_at", "-pk")[:200]
    )
    open_receipts = []
    for receipt in receipts:
        items = [_payable(receipt)] + [_payable(receipt, charge) for charge in receipt.charges.all() if charge.is_eligible]
        rows = [_item_row(item) for item in items]
        if not any(Decimal(row["remaining"]) > 0 or Decimal(row["waiting"]) > 0 for row in rows):
            continue
        open_receipts.append({
            "id": receipt.pk,
            "supplier": {"id": receipt.supplier_id, "code": receipt.supplier.code, "name": receipt.supplier.name},
            "received_on": timezone.localtime(receipt.created_at).date().isoformat(),
            "currency": receipt.currency,
            "rate_date": receipt.rate_date.isoformat() if receipt.rate_date else None,
            "items": rows,
        })
        if len(open_receipts) == 100:
            break
    payments = SupplierPayment.objects.filter(business_unit=business_unit, is_synthetic=True).select_related(
        "supplier", "paid_by", "decided_by", "charge",
    )
    approved = payments.filter(status=SupplierPayment.Status.APPROVED)
    gains = approved.filter(exchange_difference__gt=0).aggregate(total=Sum("exchange_difference"))["total"] or ZERO
    losses = approved.filter(exchange_difference__lt=0).aggregate(total=Sum("exchange_difference"))["total"] or ZERO
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "can_prepare": preparer,
        "can_approve": approver,
        "me": actor.username,
        "open_receipts": open_receipts,
        "waiting": [_payment_row(payment) for payment in payments.filter(status=SupplierPayment.Status.REQUESTED)[:100]],
        "payments": [_payment_row(payment) for payment in payments.filter(~Q(status=SupplierPayment.Status.REQUESTED))[:50]],
        "totals": {
            "exchange_gain": format(gains, "f"),
            "exchange_loss": format(-losses, "f"),
            "net": format(gains + losses, "f"),
        },
        "currency": BASE_CURRENCY,
        "policy_label": "TEST_ONLY_NOT_TIVOLI_APPROVED",
    }
