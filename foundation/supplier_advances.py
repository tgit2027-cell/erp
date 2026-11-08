"""Supplier advances: money paid to a supplier before their bill, later set against the goods.

Provisional rules (synthetic demo only, waiting for Finance's approval):

- Advances use the supplier payment permissions: whoever prepares supplier
  payments prepares an advance, and a different person with the approve
  permission approves it. Approval posts it from the bank to "advances paid to
  suppliers".
- An advance is in one currency, with the rate on the day it was paid, its
  source and date, like any supplier payment.
- When the supplier's goods arrive and their bill is recorded, the advance is
  set against them, in full or in parts, up to what is still owed on the goods.
  Each use is a supplier payment that a second person approves, whose money
  comes from the advance instead of the bank. The goods clear at the receipt
  rate and the advance at its own rate; the gap is an exchange gain or loss.
- An advance can be used only for the same supplier and in the same currency,
  and only against goods, not shipping or clearance charges.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .approval_limits import require_within_limit
from .models import UserActionGrant
from .models import Currency, JournalEntry, PurchaseReceipt, SupplierAdvance, SupplierPayment, SyntheticCounterparty
from .purchasing_services import WorkflowPermissionDenied, _exchange_terms, _to_base, require_supplier_not_frozen
from .supplier_payments import (
    _date, _payable, _payment_row, _require_user, _text, advance_left, can_approve, can_pay, request_payment,
)
from .transaction_services import InvalidTransaction, _begin_idempotent_operation, _complete, _money, _post_journal

ZERO = Decimal("0.00")
S = SupplierAdvance.Status
POLICY_LABEL = ("TEST ONLY provisional rules: advances use the supplier payment permissions and a second person approves "
                "each advance and each use; an advance is used only for the same supplier, in the same currency, against goods.")


def _require(actor, business_unit):
    _require_user(actor, business_unit)
    if not (can_pay(actor, business_unit) or can_approve(actor, business_unit)):
        raise WorkflowPermissionDenied("You need permission to prepare or approve supplier payments in this business unit.")


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _fmt(value):
    return format((value or ZERO).quantize(Decimal("0.01")), "f")


def _row(advance, actor):
    used = advance.applications.filter(status=SupplierPayment.Status.APPROVED).aggregate(total=Sum("amount"))["total"] or ZERO
    left = advance_left(advance) if advance.status == S.APPROVED else ZERO
    return {
        "id": advance.pk, "number": advance.number, "supplier": {"id": advance.supplier_id, "name": advance.supplier.name},
        "paid_on": advance.paid_on.isoformat(), "purpose": advance.purpose, "currency": advance.currency,
        "amount": _fmt(advance.amount), "exchange_rate": format(advance.exchange_rate.normalize(), "f"),
        "rate_source": advance.rate_source, "value": _fmt(advance.value), "bank_reference": advance.bank_reference,
        "status": advance.status, "status_label": advance.get_status_display(),
        "used": _fmt(used), "waiting": _fmt(advance.amount - left - used) if advance.status == S.APPROVED else "0.00",
        "left": _fmt(left), "prepared_by": _name(advance.prepared_by), "decided_by": _name(advance.decided_by),
        "decision_note": advance.decision_note, "journal_entry_id": advance.journal_entry_id,
        "can_decide": advance.status == S.REQUESTED and advance.prepared_by_id != actor.pk and can_approve(actor, advance.business_unit),
        "uses": [_payment_row(payment) | {"can_decide": payment.status == SupplierPayment.Status.REQUESTED
                                          and payment.paid_by_id != actor.pk and can_approve(actor, advance.business_unit)}
                 for payment in advance.applications.select_related("supplier", "paid_by", "decided_by", "charge").order_by("-pk")],
    }


def _open_goods(business_unit, advances):
    """Receipts of suppliers holding an unused advance, whose goods have a bill and something still to pay."""
    wanted = {(advance.supplier_id, advance.currency) for advance in advances if advance.status == S.APPROVED}
    if not wanted:
        return []
    rows = []
    receipts = (PurchaseReceipt.objects.filter(business_unit=business_unit, is_synthetic=True,
                                               supplier_id__in={supplier for supplier, _ in wanted})
                .select_related("supplier").order_by("-created_at", "-pk")[:200])
    for receipt in receipts:
        item = _payable(receipt)
        if item.bill is None or (receipt.supplier_id, item.currency) not in wanted:
            continue
        remaining = item.payable - item.committed()
        if remaining <= 0:
            continue
        rows.append({"id": receipt.pk, "supplier_id": receipt.supplier_id, "supplier": receipt.supplier.name,
                     "bill_number": item.bill.bill_number, "currency": item.currency,
                     "exchange_rate": format(item.rate.normalize(), "f"), "payable": _fmt(item.payable), "remaining": _fmt(remaining),
                     "received_on": timezone.localtime(receipt.created_at).date().isoformat()})
    return rows


def advances_overview(*, actor, business_unit, status=""):
    _require(actor, business_unit)
    advances = SupplierAdvance.objects.filter(business_unit=business_unit).select_related("supplier", "prepared_by", "decided_by")
    everything = list(advances)
    shown = [advance for advance in everything if not status or advance.status == status
             or (status == "open" and advance.status == S.APPROVED and advance_left(advance) > 0)]
    approved = [advance for advance in everything if advance.status == S.APPROVED]
    held = sum((_to_base(advance_left(advance), advance.exchange_rate) for advance in approved), ZERO)
    waiting_uses = SupplierPayment.objects.filter(advance__business_unit=business_unit, status=SupplierPayment.Status.REQUESTED)
    suppliers = SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.SUPPLIER).order_by("name")
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "advances": [_row(advance, actor) for advance in shown[:200]],
        "totals": {"held_value": _fmt(held), "open": sum(1 for advance in approved if advance_left(advance) > 0),
                   "waiting": sum(1 for advance in everything if advance.status == S.REQUESTED) + waiting_uses.count()},
        "open_goods": _open_goods(business_unit, everything) if can_pay(actor, business_unit) else [],
        "suppliers": [{"id": supplier.pk, "name": supplier.name} for supplier in suppliers] if can_pay(actor, business_unit) else [],
        "can_prepare": can_pay(actor, business_unit), "can_approve": can_approve(actor, business_unit),
        "currencies": ["QAR", *Currency.objects.filter(is_active=True).exclude(code="QAR").order_by("code").values_list("code", flat=True)],
        "currency": "QAR", "policy": POLICY_LABEL,
    }


def _next_number(business_unit, day):
    prefix = f"ADV-{business_unit.code}-{day.year}-"
    last = SupplierAdvance.objects.select_for_update().filter(number__startswith=prefix).order_by("-number").first()
    following = int(last.number.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{following:05d}"


@transaction.atomic
def prepare_advance(*, actor, business_unit, data):
    """Prepare an advance. Nothing leaves the bank until someone else approves it."""
    _require_user(actor, business_unit)
    if not can_pay(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'prepare supplier payments' permission to prepare an advance.")
    supplier = SyntheticCounterparty.objects.filter(pk=data.get("supplier_id"),
                                                    party_type=SyntheticCounterparty.Type.SUPPLIER).first()
    if supplier is None:
        raise InvalidTransaction("Choose the supplier.")
    require_supplier_not_frozen(supplier)
    amount = _money(data.get("amount"), "amount")
    if amount <= 0:
        raise InvalidTransaction("Give an amount above zero.")
    terms = _exchange_terms(data.get("currency") or "QAR", data.get("exchange_rate"), data.get("rate_source", ""),
                            data.get("rate_date"))
    paid_on = _date(data.get("paid_on"), "payment date")
    purpose = " ".join(str(data.get("purpose") or "").split())[:200]
    if not purpose:
        raise InvalidTransaction("Say what the advance is for, for example the order or proforma number.")
    advance = SupplierAdvance.objects.create(
        business_unit=business_unit, supplier=supplier, number=_next_number(business_unit, paid_on), paid_on=paid_on,
        purpose=purpose, currency=terms["currency"], amount=amount, exchange_rate=terms["exchange_rate"],
        rate_source=terms["rate_source"], rate_date=terms["rate_date"], value=_to_base(amount, terms["exchange_rate"]),
        bank_reference=_text(data.get("bank_reference"), "bank reference", 80), prepared_by=actor,
    )
    return _row(advance, actor)


@transaction.atomic
def decide_advance(*, actor, business_unit, advance_id, action, reason=""):
    _require_user(actor, business_unit)
    advance = SupplierAdvance.objects.select_for_update().filter(pk=advance_id, business_unit=business_unit).first()
    if advance is None:
        raise InvalidTransaction("That advance was not found in this business unit.")
    if advance.status != S.REQUESTED:
        raise InvalidTransaction(f"This advance is already {advance.get_status_display().lower()}.")
    if not can_approve(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'approve supplier payments' permission to approve an advance.")
    if advance.prepared_by_id == actor.pk:
        raise WorkflowPermissionDenied("You prepared this advance, so someone else must approve it.")
    advance.decided_by, advance.decided_at = actor, timezone.now()
    if action == "reject":
        reason = " ".join(str(reason or "").split())[:200]
        if not reason:
            raise InvalidTransaction("Give the reason.")
        advance.status, advance.decision_note = S.REJECTED, reason
        advance.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        return _row(advance, actor)
    if action != "approve":
        raise InvalidTransaction("Approve or reject.")
    require_within_limit(actor, business_unit, UserActionGrant.Action.APPROVE_PAYMENT, advance.value, "this advance")
    operation, _ = _begin_idempotent_operation(f"supplier-advance-{advance.pk}-post", "supplier_advance", {"advance": advance.pk})
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.SUPPLIER_ADVANCE,
        description=f"TEST ONLY: advance {advance.number} to {advance.supplier.name}"[:240],
        lines=[("TEST_SUPPLIER_ADVANCES", advance.value, ZERO, f"Advance {advance.number}: {advance.purpose}"[:200]),
               ("TEST_CASH", ZERO, advance.value, f"Advance paid to {advance.supplier.name}"[:200])],
        document_date=advance.paid_on,
    )
    _complete(operation, {"advance": advance.pk, "journal_entry_id": entry.pk})
    advance.status, advance.journal_entry = S.APPROVED, entry
    advance.save(update_fields=["status", "decided_by", "decided_at", "journal_entry"])
    return _row(advance, actor)


def use_advance(*, actor, business_unit, advance_id, receipt_id, amount, idempotency_key, paid_on=None):
    """Set part or all of an advance against the goods on a receipt. A second person approves it as a supplier payment."""
    _require_user(actor, business_unit)
    receipt = PurchaseReceipt.objects.filter(pk=receipt_id, business_unit=business_unit).first() if type(receipt_id) is int else None
    if receipt is None:
        raise InvalidTransaction(f"Choose goods received by {business_unit.name}.")
    request_payment(actor=actor, idempotency_key=idempotency_key, receipt_id=receipt.pk, amount=amount, paid_on=paid_on, advance_id=advance_id)
    advance = SupplierAdvance.objects.get(pk=advance_id)
    return _row(advance, actor)

