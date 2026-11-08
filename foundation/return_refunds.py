"""Finance review of customer returns: approve the refund or reject it.

Returns are recorded and inspected in the purchasing workbench (return_services)
and then wait here. Someone with the approve_refund permission in the business
unit, who did not record the return, decides:

- Approve: a credit note is issued against the sale's invoice. The credit first
  reduces anything the customer still owes on that invoice; the rest is
  refunded in cash or by card. The returned goods go back into stock at the
  location where the return was received, at their original cost. The journal
  debits sales returns and credits receivable and/or cash, and moves the cost
  from cost of goods sold back to inventory.
- Reject: a reason is required; nothing is posted.

Provisional defaults, to confirm with Sam:
- The credit can be any amount up to the sales value of the returned lines;
  Finance enters a lower amount (for example for damage) with a note.
- Returned goods go back to the location where the return was received,
  whatever their inspected condition; the condition is kept on the movement and
  the credit note so they can be moved, repaired or written off later.
- Refunds are paid the same day the credit note is issued.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .approval_limits import require_within_limit
from .access import active_grants
from .models import (
    AdjustmentNote, CustomerCreditNote, CustomerCreditNoteSequence, JournalEntry, QuotePayment, SalesReportLine, SalesReturnEvent,
    SalesReturnIntake, StockMovement, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .return_services import serialize_return
from .supplier_kyc import _demo_user
from .transaction_services import (
    TEST_MEMO, InvalidTransaction, _begin_idempotent_operation, _complete, _get_or_create_balances, _money,
    _post_journal, _require_demo, _require_stock_location,
)

APPROVE = UserActionGrant.Action.APPROVE_REFUND
ZERO = Decimal("0.00")
STATUSES = {"pending": ["finance_pending"], "decided": ["refunded", "rejected"]}
PAGE = 50


def can_approve_refunds(user, business_unit):
    return active_grants(user, APPROVE).filter(business_unit=business_unit).exists()


def _require_finance(user, business_unit):
    _demo_user(user)
    _require_demo(business_unit, business_unit.legal_entity)
    if not can_approve_refunds(user, business_unit):
        raise WorkflowPermissionDenied("You do not have permission to approve customer refunds for this business unit.")


def _intakes(business_unit):
    return SalesReturnIntake.objects.filter(
        quote__business_unit=business_unit, quote__is_synthetic=True, is_synthetic=True,
    ).select_related("quote__business_unit", "quote__customer", "location", "created_by")


def _issued(quote, exclude_intake=None):
    notes = CustomerCreditNote.objects.filter(intake__quote=quote)
    if exclude_intake is not None:
        notes = notes.exclude(intake=exclude_intake)
    return list(notes)


def _valuation(intake):
    """The sales value and original cost of what is being returned, line by line."""
    quote = intake.quote
    sold = {line.pk: line for line in quote.lines.select_related("variant")}
    delivered = {
        row["quote_line_id"]: row for row in SalesReportLine.objects.filter(quote_line__quote=quote)
        .values("quote_line_id").annotate(quantity=Sum("quantity"), cost=Sum("cost_of_goods_sold"))
    }
    already = {}
    for note in _issued(quote, exclude_intake=intake):
        for line in note.lines:
            already[line["quote_line_id"]] = already.get(line["quote_line_id"], ZERO) + Decimal(line["quantity"])
    lines, value, cost = [], ZERO, ZERO
    for returned in intake.lines:
        line = sold[returned["quote_line_id"]]
        quantity = Decimal(returned["quantity"])
        shipped = delivered.get(line.pk)
        if shipped is None or not shipped["quantity"]:
            raise InvalidTransaction("A returned line was never delivered, so it cannot be refunded.")
        if already.get(line.pk, ZERO) + quantity > shipped["quantity"]:
            raise InvalidTransaction("More of this item would be returned than was delivered on the sale.")
        line_value = _money(line.net_unit_price * quantity, "returned value")
        line_cost = _money(shipped["cost"] * quantity / shipped["quantity"], "returned cost")
        lines.append({
            "quote_line_id": line.pk, "variant_id": line.variant_id, "quantity": format(quantity, ".2f"),
            "net_unit_price": format(line.net_unit_price, ".2f"), "value": format(line_value, ".2f"),
            "cost": format(line_cost, ".2f"), "condition": returned.get("condition", ""),
        })
        value += line_value
        cost += line_cost
    return lines, value, cost


def _outstanding(intake):
    """What the customer still owes on the sale's invoice, after payments and earlier credit notes."""
    quote = intake.quote
    invoice = getattr(quote, "invoice", None)
    if invoice is None:
        raise InvalidTransaction("This sale has no invoice, so no credit note can be issued.")
    paid = quote.payments.filter(kind__in=[QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE]).aggregate(
        total=Sum("amount"),
    )["total"] or ZERO
    credited = sum((note.applied_to_balance for note in _issued(quote, exclude_intake=intake)), ZERO)
    credited += invoice.adjustment_notes.filter(status=AdjustmentNote.Status.APPROVED).aggregate(
        total=Sum("amount"))["total"] or ZERO
    return invoice, max(invoice.invoice_total - paid - credited, ZERO)


def _note(note):
    return {
        "number": note.number, "issued_at": note.issued_at.isoformat(),
        "issued_by": note.issued_by.get_full_name() or note.issued_by.get_username(),
        "invoice_number": note.invoice.number, "credit_total": format(note.credit_total, ".2f"),
        "applied_to_balance": format(note.applied_to_balance, ".2f"), "refunded_amount": format(note.refunded_amount, ".2f"),
        "refund_method": note.refund_method, "returned_value": format(note.returned_value, ".2f"),
        "returned_cost": format(note.returned_cost, ".2f"), "note": note.note, "lines": note.lines,
        "journal_entry_id": getattr(note.idempotency, "journal_entry", None) and note.idempotency.journal_entry.pk,
    }


def _review(intake, actor):
    data = serialize_return(intake)
    data["recorded_by"] = intake.created_by.get_full_name() or intake.created_by.get_username()
    data["customer"]["qid"] = intake.quote.customer.qid or ""
    note = getattr(intake, "credit_note", None)
    rejection = intake.events.filter(kind="refund_rejected").order_by("-occurred_at").first()
    if note is not None:
        data["credit_note"] = _note(note)
    if rejection is not None:
        data["rejection"] = {
            "reason": rejection.details.get("reason", ""), "at": rejection.occurred_at.isoformat(),
            "by": rejection.actor.get_full_name() or rejection.actor.get_username(),
        }
    if intake.status == "finance_pending":
        try:
            lines, value, cost = _valuation(intake)
            invoice, outstanding = _outstanding(intake)
            data["review"] = {
                "invoice_number": invoice.number, "returned_value": format(value, ".2f"),
                "returned_cost": format(cost, ".2f"), "outstanding_balance": format(outstanding, ".2f"),
                "lines": lines, "problem": "",
                "own_return": intake.created_by_id == actor.pk,
            }
        except InvalidTransaction as error:
            data["review"] = {"problem": str(error), "own_return": intake.created_by_id == actor.pk}
    return data


def refund_queue(*, actor, business_unit, status="pending"):
    _require_finance(actor, business_unit)
    if status not in STATUSES:
        raise InvalidTransaction("Choose returns waiting for a decision or already decided.")
    rows = list(
        _intakes(business_unit).filter(status__in=STATUSES[status])
        .prefetch_related("events__actor").order_by("-updated_at", "-pk")[:PAGE]
    )
    return {
        "status": status,
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "waiting": _intakes(business_unit).filter(status="finance_pending").count(),
        "returns": [_review(intake, actor) for intake in rows],
        "currency": "QAR",
        "policy_label": "TEST ONLY synthetic refunds and credit notes, not Tivoli-approved policy",
    }


def _locked_intake(business_unit, intake_id):
    intake = _intakes(business_unit).select_for_update(of=("self",)).filter(pk=intake_id).first()
    if intake is None:
        raise InvalidTransaction("Return not found in this business unit.")
    return intake


def _expect_pending(intake, actor):
    if intake.status != "finance_pending":
        if intake.status == "awaiting_inspection":
            raise InvalidTransaction("This return has not been inspected yet.")
        raise InvalidTransaction("This return has already been decided.")
    if intake.created_by_id == actor.pk:
        raise WorkflowPermissionDenied("You recorded this return, so someone else in Finance must decide it.")


def _next_number(business_unit, issued_at):
    year = timezone.localtime(issued_at).year
    sequence, _ = CustomerCreditNoteSequence.objects.get_or_create(business_unit=business_unit, year=year)
    sequence = CustomerCreditNoteSequence.objects.select_for_update().get(pk=sequence.pk)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return f"{business_unit.code}-CN-{year}-{sequence.last_number:05d}"


def _note_text(value, field, required=False, minimum=0):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if required and len(text) < minimum:
        raise InvalidTransaction(f"Give the {field} (at least {minimum} characters).")
    if len(text) > 240:
        raise InvalidTransaction(f"Keep the {field} under 240 characters.")
    return text


@transaction.atomic
def approve_refund(*, actor, business_unit, intake_id, idempotency_key, credit_total, refund_method, note=""):
    _require_finance(actor, business_unit)
    payload = {"intake": intake_id, "credit_total": str(credit_total), "refund_method": refund_method, "note": note}
    operation, created = _begin_idempotent_operation(idempotency_key, "customer_refund_approval", payload)
    if not created:
        return operation.result
    intake = _locked_intake(business_unit, intake_id)
    _expect_pending(intake, actor)
    if refund_method not in CustomerCreditNote.RefundMethod.values:
        raise InvalidTransaction("Choose cash or card for the refund.")
    credit = _money(credit_total, "credit amount")
    if credit <= 0:
        raise InvalidTransaction("The credit amount must be more than zero. Reject the return instead.")
    lines, value, cost = _valuation(intake)
    if credit > value:
        raise InvalidTransaction(f"The credit cannot be more than the returned goods were sold for ({value:.2f} QAR).")
    require_within_limit(actor, business_unit, APPROVE, credit, "this refund")
    text = _note_text(note, "note", required=credit < value, minimum=5)
    invoice, outstanding = _outstanding(intake)
    applied = min(credit, outstanding)
    refunded = credit - applied

    location = intake.location
    _require_stock_location(location, "customer returns")
    variants = {line.pk: line.variant for line in intake.quote.lines.select_related("variant__item__brand")}
    balances = _get_or_create_balances(business_unit, [(variants[line["quote_line_id"]], location) for line in lines])
    for line in lines:
        variant = variants[line["quote_line_id"]]
        balance = balances[(business_unit.pk, variant.pk, location.pk)]
        quantity, line_cost = Decimal(line["quantity"]), Decimal(line["cost"])
        balance.on_hand_quantity += quantity
        balance.on_hand_value += line_cost
        balance.updated_at = timezone.now()
        balance.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])
        StockMovement.objects.create(
            balance=balance, idempotency=operation, kind=StockMovement.Kind.RETURN_IN,
            quantity_delta=quantity, value_delta=line_cost,
            memo=f"{TEST_MEMO}: customer return {intake.pk}, inspected {line['condition'] or 'unknown'}",
        )
    issued_at = timezone.now()
    number = _next_number(business_unit, issued_at)
    journal = [("TEST_SALES_RETURNS", credit, ZERO, f"Credit note {number}")]
    if applied:
        journal.append(("TEST_RECEIVABLE", ZERO, applied, f"Credit note {number} against {invoice.number}"))
    if refunded:
        journal.append(("TEST_CASH", ZERO, refunded, f"Refund by {refund_method} for {number}"))
    if cost:
        journal += [
            ("TEST_INVENTORY", cost, ZERO, "Returned goods back into stock at original cost"),
            ("TEST_COGS", ZERO, cost, "Cost of returned goods reversed"),
        ]
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.SALES_RETURN, description=f"{TEST_MEMO}: customer return {intake.pk}, credit note {number}",
        lines=journal,
    )
    credit_note = CustomerCreditNote.objects.create(
        idempotency=operation, intake=intake, invoice=invoice, customer=intake.quote.customer,
        business_unit=business_unit, number=number, issued_at=issued_at, issued_by=actor, lines=lines,
        returned_value=value, returned_cost=cost, credit_total=credit, applied_to_balance=applied,
        refunded_amount=refunded, refund_method=refund_method, note=text,
    )
    intake.status = "refunded"
    intake.updated_at = issued_at
    intake.save(update_fields=["status", "updated_at"])
    SalesReturnEvent.objects.create(
        intake=intake, actor=actor, kind="refund_approved",
        details={"credit_note": number, "credit_total": format(credit, ".2f"), "journal_entry_id": entry.pk},
    )
    return _complete(operation, {**_review(intake, actor), "credit_note": _note(credit_note)})


@transaction.atomic
def reject_refund(*, actor, business_unit, intake_id, idempotency_key, reason):
    _require_finance(actor, business_unit)
    operation, created = _begin_idempotent_operation(
        idempotency_key, "customer_refund_rejection", {"intake": intake_id, "reason": reason},
    )
    if not created:
        return operation.result
    intake = _locked_intake(business_unit, intake_id)
    _expect_pending(intake, actor)
    text = _note_text(reason, "reason for rejecting", required=True, minimum=10)
    intake.status = "rejected"
    intake.updated_at = timezone.now()
    intake.save(update_fields=["status", "updated_at"])
    SalesReturnEvent.objects.create(intake=intake, actor=actor, kind="refund_rejected", details={"reason": text})
    return _complete(operation, _review(intake, actor))
