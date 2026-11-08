"""Credit and debit notes: changing what a customer owes us or what we owe a supplier, without goods moving.

Provisional rules (synthetic demo only, waiting for Finance's approval):

- A customer credit note lowers what is still owed on one invoice: a price
  agreed after the sale, a billing mistake, damaged goods the customer keeps, or
  goodwill. It can be at most what the customer still owes on that invoice, so
  it never creates money we must pay back; a paid invoice goes through returns
  and refunds instead. Returned goods keep using the return and refund screen.
- On the supplier side a note belongs to the goods on one purchase receipt that
  has a recorded bill. The supplier's credit note or our debit note lowers what
  we owe, up to what is still unpaid; the supplier's debit note raises it. The
  amount is in the receipt's currency and counts in QAR at the receipt rate.
- One person prepares a note and a different person approves it. Approval posts
  the journal; a rejected note is kept with its reason. Notes are never changed.
- Accounts: a customer credit note moves the amount from what customers owe to
  "price reductions given to customers"; a supplier note moves it between goods
  received and supplier bill differences, like an accepted bill difference.
- No tax is calculated on notes (invoices are at 0% tax for now). QAR for
  customers; the receipt's currency for suppliers, until the currency decision.
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .approval_limits import require_within_limit
from .access import active_grants
from .cash_accounts import _past_day, _text
from .models import (
    BASE_CURRENCY, AdjustmentNote, ClosedMonth, CustomerCreditNoteSequence, CustomerInvoice, DebitNoteSequence,
    DemoUserMarker, JournalEntry, PurchaseReceipt, SupplierBill, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied, _to_base
from .receivables import _open_invoices
from .supplier_payments import _payable
from .transaction_services import (
    InvalidTransaction, _begin_idempotent_operation, _complete, _money, _post_journal, _require_demo,
)

ZERO = Decimal("0.00")
PREPARE = UserActionGrant.Action.PREPARE_NOTE
APPROVE = UserActionGrant.Action.APPROVE_NOTE
K = AdjustmentNote.Kind
S = AdjustmentNote.Status
SUPPLIER_KINDS = (K.SUPPLIER_CREDIT, K.OUR_DEBIT, K.SUPPLIER_DEBIT)
POLICY_LABEL = ("TEST ONLY provisional rules: a second person approves every note; a customer credit note is at most what is "
                "still owed on the invoice; supplier notes count at the receipt's exchange rate; no tax on notes.")


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_prepare(user, business_unit):
    return _granted(user, business_unit, PREPARE)


def can_approve(user, business_unit):
    return _granted(user, business_unit, APPROVE)


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Credit and debit notes run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use credit and debit notes.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not (can_prepare(user, business_unit) or can_approve(user, business_unit)):
        raise WorkflowPermissionDenied("You need permission to prepare or approve credit and debit notes in this business unit.")


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _fmt(value):
    return format((value or ZERO).quantize(Decimal("0.01")), "f")


def _pending(queryset):
    return queryset.filter(status=S.SUBMITTED).aggregate(total=Sum("amount"))["total"] or ZERO


# --- what each document still allows ------------------------------------------------------------------------------


def _invoice_room(business_unit):
    """Open invoices of the unit with what is still owed, less notes already waiting for approval."""
    rows = []
    for row in _open_invoices(business_unit, timezone.localdate()):
        invoice = row["invoice"]
        waiting = _pending(invoice.adjustment_notes.all())
        rows.append({"invoice": invoice, "owed": row["owed"], "waiting": waiting, "room": max(row["owed"] - waiting, ZERO)})
    return rows


def _receipt_goods(receipt):
    """The goods on a receipt: what is still owed in its currency, less lowering notes waiting for approval."""
    item = _payable(receipt)
    owed = item.payable - item.committed()
    waiting = _pending(receipt.adjustment_notes.filter(kind__in=AdjustmentNote.LOWERS))
    return item, owed, waiting, max(owed - waiting, ZERO)


def _receipt_room(business_unit):
    receipts = (PurchaseReceipt.objects.filter(business_unit=business_unit, is_synthetic=True,
                                               supplier_bills__status__in=SupplierBill.PAYABLE)
                .select_related("supplier").distinct().order_by("-created_at", "-pk"))
    rows = []
    for receipt in receipts:
        item, owed, waiting, room = _receipt_goods(receipt)
        rows.append({"receipt": receipt, "item": item, "owed": owed, "waiting": waiting, "room": room})
    return rows


def _invoice_choice(row):
    invoice = row["invoice"]
    return {"id": invoice.pk, "number": invoice.number, "customer": invoice.customer.name, "customer_id": invoice.customer_id,
            "date": timezone.localtime(invoice.issued_at).date().isoformat(), "total": _fmt(invoice.invoice_total),
            "owed": _fmt(row["owed"]), "waiting": _fmt(row["waiting"]), "room": _fmt(row["room"])}


def _receipt_choice(row):
    receipt, item = row["receipt"], row["item"]
    return {"id": receipt.pk, "supplier": receipt.supplier.name, "supplier_id": receipt.supplier_id,
            "date": timezone.localtime(receipt.created_at).date().isoformat(), "bill_number": item.bill.bill_number if item.bill else "",
            "currency": item.currency, "rate": format(item.rate.normalize(), "f"), "payable": _fmt(item.payable),
            "owed": _fmt(row["owed"]), "waiting": _fmt(row["waiting"]), "room": _fmt(row["room"])}


# --- rows ---------------------------------------------------------------------------------------------------------


def _row(note, actor):
    document = (note.invoice.number if note.invoice_id else
                f"Receipt {note.receipt_id}" + (f" · bill {bill.bill_number}" if (bill := note.receipt.supplier_bills.filter(
                    status__in=SupplierBill.PAYABLE).first()) else ""))
    lowers = note.kind in AdjustmentNote.LOWERS
    return {
        "id": note.pk, "kind": note.kind, "kind_label": note.get_kind_display(), "side": "customer" if note.invoice_id else "supplier",
        "lowers": lowers, "effect": ("Lowers what the customer owes" if note.invoice_id else
                                     "Lowers what we owe" if lowers else "Raises what we owe"),
        "number": note.number, "their_reference": note.their_reference, "date": note.note_date.isoformat(),
        "counterparty": note.counterparty.name, "document": document, "invoice_id": note.invoice_id, "receipt_id": note.receipt_id,
        "reason": note.reason, "reason_label": note.get_reason_display(), "description": note.description,
        "currency": note.currency, "amount": _fmt(note.amount), "value": _fmt(note.value),
        "status": note.status, "status_label": note.get_status_display(),
        "prepared_by": _name(note.prepared_by), "prepared_at": note.prepared_at.isoformat(),
        "decided_by": _name(note.decided_by), "decided_at": note.decided_at.isoformat() if note.decided_at else "",
        "decision_note": note.decision_note, "journal_entry_id": note.journal_entry_id,
        "can_decide": note.status == S.SUBMITTED and note.prepared_by_id != actor.pk and can_approve(actor, note.business_unit),
    }


def notes_overview(*, actor, business_unit, side="customer", status="", search=""):
    _require_user(actor, business_unit)
    notes = AdjustmentNote.objects.filter(business_unit=business_unit).select_related(
        "counterparty", "invoice", "receipt", "prepared_by", "decided_by")
    side_filter = Q(kind=K.CUSTOMER_CREDIT) if side == "customer" else Q(kind__in=SUPPLIER_KINDS)
    waiting = AdjustmentNote.objects.filter(business_unit=business_unit, status=S.SUBMITTED)
    notes = notes.filter(side_filter)
    if status:
        notes = notes.filter(status=status)
    search = " ".join(str(search or "").split())[:80]
    if search:
        notes = notes.filter(Q(counterparty__name__icontains=search) | Q(number__icontains=search)
                             | Q(their_reference__icontains=search) | Q(description__icontains=search)
                             | Q(invoice__number__icontains=search))
    preparer = can_prepare(actor, business_unit)
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "side": side,
        "notes": [_row(note, actor) for note in notes[:200]],
        "waiting": {"customer": waiting.filter(kind=K.CUSTOMER_CREDIT).count(),
                    "supplier": waiting.filter(kind__in=SUPPLIER_KINDS).count(),
                    "for_me": waiting.exclude(prepared_by=actor).count() if can_approve(actor, business_unit) else 0},
        "invoices": [_invoice_choice(row) for row in _invoice_room(business_unit) if row["room"] > 0] if preparer and side == "customer" else [],
        "receipts": [_receipt_choice(row) for row in _receipt_room(business_unit)] if preparer and side == "supplier" else [],
        "kinds": [{"value": value, "label": label} for value, label in K.choices],
        "reasons": [{"value": value, "label": label} for value, label in AdjustmentNote.Reason.choices],
        "can_prepare": preparer, "can_approve": can_approve(actor, business_unit),
        "currency": BASE_CURRENCY, "policy": POLICY_LABEL,
    }


# --- prepare and decide -------------------------------------------------------------------------------------------


def _check_open(business_unit, day):
    if ClosedMonth.objects.filter(business_unit=business_unit, month=day.replace(day=1)).exists():
        raise InvalidTransaction(f"{day:%B %Y} is closed for {business_unit.name}. Use a date in an open month.")


def _choice(value, choices, field):
    if value not in {key for key, _ in choices}:
        raise InvalidTransaction(f"Choose the {field}.")
    return value


@transaction.atomic
def prepare_note(*, actor, business_unit, data):
    """Record a note and send it for approval. Nothing is posted until someone else approves it."""
    _require_user(actor, business_unit)
    if not can_prepare(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'prepare credit and debit notes' permission for this unit.")
    kind = _choice(data.get("kind"), K.choices, "kind of note")
    reason = _choice(data.get("reason"), AdjustmentNote.Reason.choices, "reason")
    description = _text(data.get("description"), "description", 200, required=True)
    day = _past_day(data.get("date") or timezone.localdate().isoformat(), "date")
    _check_open(business_unit, day)
    amount = _money(data.get("amount"), "amount")
    if amount <= 0:
        raise InvalidTransaction("Give an amount above zero.")
    fields = {}
    if kind == K.CUSTOMER_CREDIT:
        invoice = CustomerInvoice.objects.select_for_update().filter(
            pk=data.get("invoice_id"), business_unit=business_unit, is_synthetic=True).first()
        if invoice is None:
            raise InvalidTransaction(f"Choose an invoice of {business_unit.name}.")
        if day < timezone.localtime(invoice.issued_at).date():
            raise InvalidTransaction(f"The credit note cannot be dated before invoice {invoice.number}.")
        room = next((row["room"] for row in _invoice_room(business_unit) if row["invoice"].pk == invoice.pk), ZERO)
        if amount > room:
            raise InvalidTransaction(
                f"{invoice.number} has {_fmt(room)} QAR left to credit. A credit note cannot be more than what is still owed; "
                "for a paid invoice use returns and refunds.")
        fields = {"invoice": invoice, "counterparty": invoice.customer, "currency": BASE_CURRENCY, "rate": Decimal("1"),
                  "value": amount}
    else:
        receipt = PurchaseReceipt.objects.select_for_update().filter(
            pk=data.get("receipt_id"), business_unit=business_unit, is_synthetic=True).first()
        if receipt is None:
            raise InvalidTransaction(f"Choose a purchase receipt of {business_unit.name}.")
        item, owed, waiting, room = _receipt_goods(receipt)
        if item.bill is None:
            raise InvalidTransaction(f"Record and settle the supplier's bill for receipt {receipt.pk} first.")
        if kind in AdjustmentNote.LOWERS and amount > room:
            raise InvalidTransaction(
                f"Receipt {receipt.pk} has {_fmt(room)} {item.currency} still unpaid. A note cannot lower what we owe below "
                "what is already paid or waiting to be paid.")
        reference = _text(data.get("their_reference"), "supplier's note number", 40, required=kind != K.OUR_DEBIT)
        fields = {"receipt": receipt, "counterparty": receipt.supplier, "currency": item.currency, "rate": item.rate,
                  "value": amount if item.currency == BASE_CURRENCY else _to_base(amount, item.rate),
                  "their_reference": reference}
        if fields["value"] <= 0:
            raise InvalidTransaction("The amount is too small to count in QAR.")
    note = AdjustmentNote.objects.create(business_unit=business_unit, kind=kind, note_date=day, reason=reason,
                                         description=description, amount=amount, prepared_by=actor, **fields)
    return _row(note, actor)


def _next_number(note):
    year = note.note_date.year
    model, letters = (CustomerCreditNoteSequence, "CN") if note.kind == K.CUSTOMER_CREDIT else (DebitNoteSequence, "DN")
    sequence, _ = model.objects.get_or_create(business_unit=note.business_unit, year=year)
    sequence = model.objects.select_for_update().get(pk=sequence.pk)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return f"{note.business_unit.code}-{letters}-{year}-{sequence.last_number:05d}"


def _journal_lines(note):
    what = note.number or note.their_reference
    if note.kind == K.CUSTOMER_CREDIT:
        return [("TEST_SALES_ALLOWANCES", note.value, ZERO, f"Credit note {what}: {note.get_reason_display().lower()}"),
                ("TEST_RECEIVABLE", ZERO, note.value, f"Credit note {what} against {note.invoice.number}")]
    memo = f"{note.get_kind_display()} {what} on receipt {note.receipt_id}"
    if note.kind in AdjustmentNote.LOWERS:
        return [("TEST_GRNI", note.value, ZERO, memo), ("TEST_BILL_DIFFERENCES", ZERO, note.value, memo)]
    return [("TEST_BILL_DIFFERENCES", note.value, ZERO, memo), ("TEST_GRNI", ZERO, note.value, memo)]


@transaction.atomic
def decide_note(*, actor, business_unit, note_id, action, reason=""):
    _require_user(actor, business_unit)
    note = AdjustmentNote.objects.select_for_update().filter(pk=note_id, business_unit=business_unit).first()
    if note is None:
        raise InvalidTransaction("That note was not found in this business unit.")
    if note.status != S.SUBMITTED:
        raise InvalidTransaction(f"This note is already {note.get_status_display().lower()}.")
    if not can_approve(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'approve credit and debit notes' permission for this unit.")
    if note.prepared_by_id == actor.pk:
        raise WorkflowPermissionDenied("Someone other than the person who prepared this note must approve it.")
    note.decided_by, note.decided_at = actor, timezone.now()
    if action == "reject":
        note.status, note.decision_note = S.REJECTED, _text(reason, "reason", 200, required=True)
        note.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        return _row(note, actor)
    if action != "approve":
        raise InvalidTransaction("Approve or reject.")
    require_within_limit(actor, business_unit, APPROVE, _to_base(note.amount, note.rate), "this note")
    _check_open(business_unit, note.note_date)
    # Check again: payments or other notes may have changed what is owed since it was prepared.
    if note.kind == K.CUSTOMER_CREDIT:
        owed = next((row["owed"] for row in _open_invoices(business_unit, timezone.localdate())
                     if row["invoice"].pk == note.invoice_id), ZERO)
        if note.amount > owed:
            raise InvalidTransaction(f"{note.invoice.number} now has only {_fmt(owed)} QAR owed. Reject this note and prepare a new one.")
    elif note.kind in AdjustmentNote.LOWERS:
        _, owed, _, _ = _receipt_goods(PurchaseReceipt.objects.select_for_update().get(pk=note.receipt_id))
        if note.amount > owed:
            raise InvalidTransaction(f"Receipt {note.receipt_id} now has only {_fmt(owed)} {note.currency} unpaid. "
                                     "Reject this note and prepare a new one.")
    if note.kind in (K.CUSTOMER_CREDIT, K.OUR_DEBIT):
        note.number = _next_number(note)
    operation, _ = _begin_idempotent_operation(f"adjustment-note-{note.pk}-post", "adjustment_note", {"note": note.pk})
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.ADJUSTMENT_NOTE,
        description=f"TEST ONLY: {note.get_kind_display().lower()} {note.number or note.their_reference}, {note.counterparty.name}"[:240],
        lines=_journal_lines(note), document_date=note.note_date,
    )
    _complete(operation, {"note": note.pk, "journal_entry_id": entry.pk})
    note.status, note.journal_entry, note.decision_note = S.APPROVED, entry, _text(reason, "note", 200)
    note.save(update_fields=["status", "number", "decided_by", "decided_at", "decision_note", "journal_entry"])
    return _row(note, actor)


def note_detail(*, actor, business_unit, note_id):
    _require_user(actor, business_unit)
    note = AdjustmentNote.objects.filter(pk=note_id, business_unit=business_unit).first()
    if note is None:
        raise InvalidTransaction("That note was not found in this business unit.")
    row = _row(note, actor)
    if note.invoice_id:
        owed = next((r["owed"] for r in _open_invoices(business_unit, timezone.localdate()) if r["invoice"].pk == note.invoice_id), ZERO)
        row["document_now"] = {"total": _fmt(note.invoice.invoice_total), "owed": _fmt(owed), "currency": BASE_CURRENCY}
    else:
        item, owed, _, _ = _receipt_goods(note.receipt)
        row["document_now"] = {"total": _fmt(item.payable), "owed": _fmt(owed), "currency": item.currency}
    return row

