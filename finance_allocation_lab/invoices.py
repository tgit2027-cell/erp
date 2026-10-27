"""Explicit supplier-invoice proposals only; no supplier master or payable creation."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Func, TextField, Value
from foundation.finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _require_synthetic_demo,
)
from .budget import _date
from .expenses import _text
from .forecasts import amount
from .services import _lines
from .models import SupplierInvoiceDraft, SupplierInvoiceRevision


def prepare_payload(data, entity):
    _require_synthetic_demo(entity)
    fields = {"supplier_reference", "invoice_reference", "invoice_date", "due_date", "currency", "lines"}
    if not isinstance(data, dict) or not fields <= set(data) <= fields | {"expected_revision"}:
        raise FinanceDraftValidationError("Use explicit proposal fields only; no bank, tax, approval, payment or posting inputs.")
    if data["currency"] != "QAR":
        raise FinanceDraftValidationError("Explicit QAR is required; no FX is supported.")
    raw = data["lines"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 200:
        raise FinanceDraftValidationError("Enter between 1 and 200 explicit proposal lines (technical limit).")
    lines = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict) or set(item) != {
            "description", "amount", "account_id", "business_unit_id", "dimension_value_ids"
        }:
            raise FinanceDraftValidationError(f"Line {index}: enter description, amount and explicit saved references only.")
        ids = item["dimension_value_ids"]
        if (not isinstance(ids, list) or
            any(isinstance(pk, bool) or not isinstance(pk, int) or pk < 1 for pk in ids) or
            len(ids) != len(set(ids))):
            raise FinanceDraftValidationError(f"Line {index}: choose distinct saved dimension-value IDs.")
        resolved = _lines({"title_en": "Explicit invoice proposal context", "lines": [{
            "account_id": item["account_id"], "business_unit_id": item["business_unit_id"],
            "dimension_value_ids": ids, "debit": "0.00", "credit": "0.00",
        }]}, entity)[0]
        try:
            value = amount(item["amount"])
        except FinanceDraftValidationError:
            raise FinanceDraftValidationError(
                f"Line {index}: enter an exact non-negative QAR string up to "
                "999999999999.99 with at most two decimal places; no rounding is inferred.") from None
        lines.append({
            "description": _text(item["description"], f"Line {index} description", 2000, required=True),
            "amount": format(value, ".2f"),
            **{key: resolved[key] for key in ("account_id", "business_unit_id", "dimension_value_ids")},
        })
    return {
        "supplier_reference": _text(data["supplier_reference"], "Synthetic supplier reference", 200, required=True),
        "invoice_reference": _text(data["invoice_reference"], "Synthetic invoice reference", 200, required=True),
        "invoice_date": _date(data["invoice_date"], "invoice date").isoformat(),
        "due_date": None if data["due_date"] is None else _date(data["due_date"], "optional explicit due date").isoformat(),
        "currency": "QAR", "lines": lines,
    }


def review(payload):
    return {"currency": "QAR", "total": format(sum(
        (Decimal(line["amount"]) for line in payload["lines"]), Decimal("0.00")), ".2f"),
        "proposed": True, "operational_payable": False, "approved": False, "posted": False}


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def duplicate_warning(row):
    # Exact case-sensitive references after surrounding ASCII whitespace only. This
    # advisory observation is not an atomic uniqueness policy or save gate.
    from django.db.models.fields.json import KeyTextTransform
    matches = SupplierInvoiceDraft.objects.filter(
        legal_entity=row.legal_entity, is_synthetic=True
    ).exclude(pk=row.pk).annotate(
        supplier_match=Func(KeyTextTransform("supplier_reference", "payload"),
                            Value(" \t\n\r\v\f"), function="BTRIM", output_field=TextField()),
        invoice_match=Func(KeyTextTransform("invoice_reference", "payload"),
                           Value(" \t\n\r\v\f"), function="BTRIM", output_field=TextField()),
    ).filter(
        supplier_match=row.payload["supplier_reference"].strip(" \t\n\r\v\f"),
        invoice_match=row.payload["invoice_reference"].strip(" \t\n\r\v\f"),
    ).order_by("pk")
    found = list(matches.values("id", "revision"))
    return {"possible_duplicate": bool(found), "matches": found}


def record_revision(row):
    SupplierInvoiceRevision.objects.create(draft=row, revision=row.revision, snapshot=snapshot(row))


def save_invoice(*, data, entity, draft_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if draft_id is None:
            if "expected_revision" in data:
                raise FinanceDraftValidationError("Expected revision is for edits; reopen a saved proposal.")
            row = SupplierInvoiceDraft.objects.create(legal_entity=entity, payload=payload)
        else:
            row = SupplierInvoiceDraft.objects.select_for_update().get(
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
