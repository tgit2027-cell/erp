"""Non-posting fixed-asset inputs; no capitalization, valuation or custody policy."""
import re

from django.db import transaction
from foundation.finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _money, _require_synthetic_demo,
)
from .budget import _date
from .expenses import _text
from .services import _lines
from .models import FixedAssetDraft, FixedAssetRevision


def prepare_payload(data, entity):
    _require_synthetic_demo(entity)
    fields = {"asset_identifier", "name", "description", "acquisition_date", "currency", "cost",
              "account_id", "business_unit_id", "dimension_value_ids", "reference", "location_details"}
    if not isinstance(data, dict) or not fields <= set(data) <= fields | {"expected_revision"}:
        raise FinanceDraftValidationError("Use explicit fixed-asset draft fields only; no depreciation, capitalization, custody, approval, disposal or posting fields.")
    if data["currency"] != "QAR":
        raise FinanceDraftValidationError("Explicit QAR is required; no currency conversion is supported.")
    amount = data["cost"]
    if not isinstance(amount, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", amount.strip()):
        raise FinanceDraftValidationError("Enter an exact non-negative proposed QAR cost with at most two decimal places; no rounding is inferred.")
    amount = _money(amount.strip())
    dimensions = data["dimension_value_ids"]
    if (not isinstance(dimensions, list) or
        any(isinstance(pk, bool) or not isinstance(pk, int) or pk < 1 for pk in dimensions) or
        len(dimensions) != len(set(dimensions))):
        raise FinanceDraftValidationError("Choose distinct explicit saved dimension-value IDs.")
    resolved = _lines({"title_en": "Explicit proposed fixed-asset context", "lines": [{
        "account_id": data["account_id"], "business_unit_id": data["business_unit_id"],
        "dimension_value_ids": dimensions, "debit": "0.00", "credit": "0.00",
    }]}, entity)[0]  # Existing validation only: no journal or asset posting.
    return {
        "asset_identifier": _text(data["asset_identifier"], "Synthetic asset identifier", 100, required=True),
        "name": _text(data["name"], "Asset name", 200, required=True),
        "description": _text(data["description"], "Asset description", 2000, required=True),
        "acquisition_date": _date(data["acquisition_date"], "explicit acquisition date").isoformat(),
        "currency": "QAR", "cost": format(amount, ".2f"),
        "account_id": resolved["account_id"], "business_unit_id": resolved["business_unit_id"],
        "dimension_value_ids": sorted(resolved["dimension_value_ids"]),
        "reference": _text(data["reference"], "Optional synthetic reference", 200),
        "location_details": _text(data["location_details"], "Optional proposed location details", 2000),
    }


def review(payload):
    return {"currency": "QAR", "proposed_cost": payload["cost"],
            "capitalized": False, "posted": False, "approved": False}


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def record_revision(row):
    FixedAssetRevision.objects.create(draft=row, revision=row.revision, snapshot=snapshot(row))


def save_asset(*, data, entity, draft_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if draft_id is None:
            if "expected_revision" in data:
                raise FinanceDraftValidationError("Expected revision is for updates; reopen a saved draft to edit it.")
            row = FixedAssetDraft.objects.create(legal_entity=entity, payload=payload)
        else:
            row = FixedAssetDraft.objects.select_for_update().get(
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
