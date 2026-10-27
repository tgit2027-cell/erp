"""Explicit internal credit magnitudes, not signed receivable adjustments."""
from django.db import transaction
from foundation.finance_drafts import FinanceDraftConflict, FinanceDraftValidationError
from .expenses import _text
from .customer_invoices import prepare_payload as prepare_invoice, review as invoice_review
from .models import CustomerCreditDraft, CustomerCreditRevision, CustomerInvoiceDraft


def prepare_payload(data, entity):
    fields = {"customer_reference", "document_reference", "proposal_date", "reason",
              "linked_invoice_id", "currency", "lines"}
    if not isinstance(data, dict) or not fields <= set(data) <= fields | {"expected_revision"}:
        raise FinanceDraftValidationError(
            "Use explicit credit proposal fields only; no refund, settlement, tax, approval or posting inputs.")
    invoice = prepare_invoice({
        "customer_reference": data["customer_reference"], "customer_details": "",
        "invoice_reference": data["document_reference"], "invoice_date": data["proposal_date"],
        "due_date": None, "currency": data["currency"], "lines": data["lines"],
    }, entity)
    linked = data["linked_invoice_id"]
    if linked is not None and (isinstance(linked, bool) or not isinstance(linked, int) or linked < 1):
        raise FinanceDraftValidationError("Select an existing synthetic customer-invoice proposal or leave the link empty.")
    return {
        "customer_reference": invoice["customer_reference"],
        "document_reference": invoice["invoice_reference"], "proposal_date": invoice["invoice_date"],
        "reason": _text(data["reason"], "Explicit proposal reason", 2000, required=True),
        "linked_invoice_id": linked, "currency": "QAR", "lines": invoice["lines"],
    }


def review(payload):
    return {
        "currency": "QAR", "total": invoice_review(payload)["total"],
        "amount_basis": "explicit_non_negative_magnitudes",
        "interpretation": (
            "Customer credit proposal classification only. Amounts are entered non-negative magnitudes; "
            "no signed receivable effect or account mapping is assigned."
        ),
        "proposed": True, "issued_credit_note": False, "refund": False, "settlement": False,
        "operational_receivable_adjustment": False, "approved": False, "posted": False,
    }


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload,
            "linked_invoice": row.invoice_reference_snapshot, "review": review(row.payload)}


def record_revision(row):
    CustomerCreditRevision.objects.create(draft=row, revision=row.revision, snapshot=snapshot(row))


def save_credit(*, data, entity, draft_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        row = None
        if draft_id is not None:
            row = CustomerCreditDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=entity, is_synthetic=True)
            expected = data.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
                raise FinanceDraftValidationError("An integer expected revision is required.")
            if expected != row.revision:
                raise FinanceDraftConflict(row.revision)
        elif "expected_revision" in data:
            raise FinanceDraftValidationError("Expected revision is for edits; reopen a saved proposal.")
        linked, link_snapshot = None, None
        if payload["linked_invoice_id"] is not None:
            # Own row, then source row: same order on all edits. Locking the
            # source prevents capturing a mixture of reference/revision facts.
            linked = CustomerInvoiceDraft.objects.select_for_update().filter(
                pk=payload["linked_invoice_id"], legal_entity=entity, is_synthetic=True).first()
            if linked is None:
                raise FinanceDraftValidationError("Linked synthetic customer-invoice proposal is unavailable in this entity.")
            if linked.payload["customer_reference"].strip(" \t\n\r\v\f") != payload["customer_reference"].strip(" \t\n\r\v\f"):
                raise FinanceDraftValidationError("Linked invoice customer reference must match exactly apart from surrounding ASCII whitespace.")
            link_snapshot = {
                "id": linked.pk, "revision": linked.revision,
                "customer_reference": linked.payload["customer_reference"],
                "invoice_reference": linked.payload["invoice_reference"],
            }
        if row is None:
            row = CustomerCreditDraft.objects.create(
                legal_entity=entity, payload=payload, linked_invoice=linked,
                invoice_reference_snapshot=link_snapshot)
        else:
            row.payload, row.linked_invoice, row.invoice_reference_snapshot = payload, linked, link_snapshot
            row.revision += 1
            row.save(update_fields=["payload", "linked_invoice", "invoice_reference_snapshot", "revision", "updated_at"])
        record_revision(row)
        return row
