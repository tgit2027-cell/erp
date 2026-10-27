"""Explicit unpaid/unapplied proposals only; never payments or payables."""
from django.db import transaction
from foundation.finance_drafts import FinanceDraftConflict, FinanceDraftValidationError, _require_synthetic_demo
from foundation.models import BusinessUnit, FinanceDraftAccount, FinanceDraftDimensionValue
from .budget import _date
from .expenses import _text
from .forecasts import amount
from .models import SupplierAdvanceDraft, SupplierAdvanceRevision


def prepare_payload(data, entity):
    _require_synthetic_demo(entity)
    fields = {"supplier_reference", "document_reference", "proposed_date", "amount", "purpose",
              "currency", "account_id", "business_unit_id", "dimension_value_ids"}
    if not isinstance(data, dict) or not fields <= set(data) <= fields | {"expected_revision"}:
        raise FinanceDraftValidationError(
            "Use explicit advance proposal fields only; no bank, terms, payment, tax, approval, application or posting inputs.")
    if data["currency"] != "QAR":
        raise FinanceDraftValidationError("Explicit QAR is required; no FX is supported.")
    account, unit, ids = data["account_id"], data["business_unit_id"], data["dimension_value_ids"]
    for value, query, label in (
        (account, FinanceDraftAccount.objects.filter(legal_entity=entity, is_synthetic=True), "account"),
        (unit, BusinessUnit.objects.filter(legal_entity=entity, is_synthetic=True, is_active=True), "business unit"),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1 or
            not query.filter(pk=value).exists()
        ):
            raise FinanceDraftValidationError(f"Optional {label} is unavailable in this synthetic entity.")
    if (not isinstance(ids, list) or
        any(isinstance(pk, bool) or not isinstance(pk, int) or pk < 1 for pk in ids) or
        len(set(ids)) != len(ids)):
        raise FinanceDraftValidationError("Choose distinct explicit dimension-value IDs or none.")
    valid = FinanceDraftDimensionValue.objects.filter(
        pk__in=ids, is_synthetic=True, dimension__is_synthetic=True, dimension__legal_entity=entity)
    if valid.count() != len(ids):
        raise FinanceDraftValidationError("A saved dimension value is unavailable in this synthetic entity.")
    return {
        "supplier_reference": _text(data["supplier_reference"], "Synthetic supplier reference", 200, required=True),
        "document_reference": _text(data["document_reference"], "Proposal reference", 200, required=True),
        "proposed_date": _date(data["proposed_date"], "proposed date").isoformat(),
        "amount": format(amount(data["amount"]), ".2f"),
        "purpose": _text(data["purpose"], "Explicit proposal purpose", 2000, required=True),
        "currency": "QAR", "account_id": account, "business_unit_id": unit, "dimension_value_ids": sorted(ids),
    }


def review(payload):
    return {
        "currency": "QAR", "total": payload["amount"], "proposed": True,
        "unpaid": True, "unapplied": True, "actual_payment": False,
        "invoice_application": False, "operational_payable_change": False, "approved": False, "posted": False,
    }


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def record_revision(row):
    SupplierAdvanceRevision.objects.create(draft=row, revision=row.revision, snapshot=snapshot(row))


def save_advance(*, data, entity, draft_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if draft_id is None:
            if "expected_revision" in data:
                raise FinanceDraftValidationError("Expected revision is for edits; reopen a saved proposal.")
            row = SupplierAdvanceDraft.objects.create(legal_entity=entity, payload=payload)
        else:
            row = SupplierAdvanceDraft.objects.select_for_update().get(
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
