"""Internal AR review drafts only; never issue documents or create receivables."""
from django.db import transaction
from django.db.models import Func, TextField, Value
from django.db.models.fields.json import KeyTextTransform
from foundation.finance_drafts import FinanceDraftConflict, FinanceDraftValidationError
from .expenses import _text
from .invoices import prepare_payload as prepare_supplier, review as review_supplier
from .models import CustomerInvoiceDraft, CustomerInvoiceRevision


def prepare_payload(data, entity):
    keys = {"customer_reference", "customer_details", "invoice_reference", "invoice_date",
            "due_date", "currency", "lines"}
    if not isinstance(data, dict) or not keys <= set(data) <= keys | {"expected_revision"}:
        raise FinanceDraftValidationError(
            "Use explicit internal proposal fields only; no customer-master, credit, tax, terms, payment or posting inputs.")
    # Reuse the established date/decimal/account/BU/dimension resolver. The
    # adapter does not create/read suppliers or operational customer records.
    try:
        prepared = prepare_supplier({
            "supplier_reference": data["customer_reference"],
            **{key: data[key] for key in ("invoice_reference", "invoice_date", "due_date", "currency", "lines")},
        }, entity)
    except FinanceDraftValidationError as exc:
        raise FinanceDraftValidationError(str(exc).replace("supplier reference", "customer reference")) from None
    return {
        "customer_reference": prepared.pop("supplier_reference"),
        "customer_details": _text(data["customer_details"], "Synthetic customer details", 2000),
        **prepared,
    }


def review(payload):
    return {
        "currency": "QAR", "total": review_supplier(payload)["total"],
        "proposed": True, "issued_customer_invoice": False,
        "operational_receivable": False, "approved": False, "posted": False,
    }


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def duplicate_warning(row):
    matches = CustomerInvoiceDraft.objects.filter(
        legal_entity=row.legal_entity, is_synthetic=True
    ).exclude(pk=row.pk).annotate(
        customer_match=Func(KeyTextTransform("customer_reference", "payload"),
                            Value(" \t\n\r\v\f"), function="BTRIM", output_field=TextField()),
        invoice_match=Func(KeyTextTransform("invoice_reference", "payload"),
                           Value(" \t\n\r\v\f"), function="BTRIM", output_field=TextField()),
    ).filter(
        customer_match=row.payload["customer_reference"].strip(" \t\n\r\v\f"),
        invoice_match=row.payload["invoice_reference"].strip(" \t\n\r\v\f"),
    ).order_by("pk")
    found = list(matches.values("id", "revision"))
    return {"possible_duplicate": bool(found), "matches": found}


def record_revision(row):
    CustomerInvoiceRevision.objects.create(draft=row, revision=row.revision, snapshot=snapshot(row))


def save_invoice(*, data, entity, draft_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if draft_id is None:
            if "expected_revision" in data:
                raise FinanceDraftValidationError("Expected revision is for edits; reopen a saved proposal.")
            row = CustomerInvoiceDraft.objects.create(legal_entity=entity, payload=payload)
        else:
            row = CustomerInvoiceDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=entity, is_synthetic=True)
            expected = data.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
                raise FinanceDraftValidationError("An integer expected revision is required.")
            if expected != row.revision:
                raise FinanceDraftConflict(row.revision)
            row.payload, row.revision = payload, row.revision + 1
            row.save(update_fields=["payload", "revision", "updated_at"])
        record_revision(row)
        return row
