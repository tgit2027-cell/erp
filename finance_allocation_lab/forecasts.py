"""Manual scenario arithmetic only: no actual balances, timing inference or posting."""
import re
from decimal import Decimal

from django.db import transaction
from foundation.finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _money, _require_synthetic_demo,
)
from foundation.models import BusinessUnit, FinanceDraftDimensionValue
from .budget import _date
from .expenses import _text
from .models import CashForecastDraft, CashForecastRevision


def amount(value, *, signed=False):
    pattern = r"-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?" if signed else r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?"
    if not isinstance(value, str) or not re.fullmatch(pattern, value.strip()):
        raise FinanceDraftValidationError(
            "Enter explicit exact QAR strings up to 999999999999.99, with at most two decimal places. "
            "Only the opening balance may be negative; use a flow direction for entries.")
    value = value.strip()
    absolute = _money(value.lstrip("-"))
    return -absolute if value.startswith("-") else absolute


def prepare_payload(data, entity):
    _require_synthetic_demo(entity)
    fields = {"name", "as_of_date", "opening_balance", "currency", "entries"}
    if not isinstance(data, dict) or not fields <= set(data) <= fields | {"expected_revision"}:
        raise FinanceDraftValidationError("Use explicit cash-forecast scenario fields only; no actual cash, approval, scheduling or posting inputs.")
    if data["currency"] != "QAR":
        raise FinanceDraftValidationError("Explicit QAR is required; no FX is supported.")
    start = _date(data["as_of_date"], "opening as-of date")
    opening = amount(data["opening_balance"], signed=True)
    raw = data["entries"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 200:
        raise FinanceDraftValidationError("Enter between 1 and 200 explicit proposal rows (technical worksheet limit).")
    entries = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict) or set(item) != {
            "date", "direction", "amount", "description", "business_unit_id", "dimension_value_ids"
        }:
            raise FinanceDraftValidationError(f"Flow {index}: enter explicit date, direction, amount, description and optional saved references only.")
        day = _date(item["date"], f"flow {index} date")
        if day < start:
            raise FinanceDraftValidationError(f"Flow {index}: date cannot precede the opening as-of date.")
        if item["direction"] not in ("inflow", "outflow"):
            raise FinanceDraftValidationError(f"Flow {index}: choose inflow or outflow explicitly.")
        unit = item["business_unit_id"]
        if unit is not None and (
            isinstance(unit, bool) or not isinstance(unit, int) or unit < 1 or
            not BusinessUnit.objects.filter(pk=unit, legal_entity=entity, is_synthetic=True, is_active=True).exists()
        ):
            raise FinanceDraftValidationError(f"Flow {index}: business unit is unavailable in this synthetic entity.")
        ids = item["dimension_value_ids"]
        if (not isinstance(ids, list) or any(isinstance(pk, bool) or not isinstance(pk, int) or pk < 1 for pk in ids)
            or len(set(ids)) != len(ids)):
            raise FinanceDraftValidationError(f"Flow {index}: choose distinct explicit dimension-value IDs.")
        valid = FinanceDraftDimensionValue.objects.filter(
            pk__in=ids, is_synthetic=True, dimension__is_synthetic=True, dimension__legal_entity=entity)
        if valid.count() != len(ids):
            raise FinanceDraftValidationError(f"Flow {index}: a saved dimension value is unavailable in this synthetic entity.")
        entries.append({
            "date": day.isoformat(), "direction": item["direction"],
            "amount": format(amount(item["amount"]), ".2f"),
            "description": _text(item["description"], f"Flow {index} description", 2000, required=True),
            "business_unit_id": unit, "dimension_value_ids": sorted(ids),
        })
    return {
        "name": _text(data["name"], "Forecast name", 200, required=True),
        "as_of_date": start.isoformat(), "opening_balance": format(opening, ".2f"),
        "currency": "QAR", "entries": entries,
    }


def review(payload):
    opening = balance = Decimal(payload["opening_balance"])
    inflows = outflows = Decimal("0.00")
    rows = []
    for index, entry in sorted(enumerate(payload["entries"], 1), key=lambda pair: (pair[1]["date"], pair[0])):
        value = Decimal(entry["amount"])
        if entry["direction"] == "inflow":
            inflows += value
            balance += value
        else:
            outflows += value
            balance -= value
        rows.append({**entry, "line_number": index, "running_balance": format(balance, ".2f")})
    return {
        "currency": "QAR", "opening_balance": format(opening, ".2f"),
        "total_inflows": format(inflows, ".2f"), "total_outflows": format(outflows, ".2f"),
        "net_change": format(inflows - outflows, ".2f"), "closing_balance": format(balance, ".2f"),
        "ordering": "date_ascending_then_input_order", "entries": rows,
        "proposed": True, "actual_cash": False, "posted": False, "approved": False,
    }


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def record_revision(row):
    CashForecastRevision.objects.create(draft=row, revision=row.revision, snapshot=snapshot(row))


def save_forecast(*, data, entity, draft_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if draft_id is None:
            if "expected_revision" in data:
                raise FinanceDraftValidationError("Expected revision is for edits; reopen a saved scenario.")
            row = CashForecastDraft.objects.create(legal_entity=entity, payload=payload)
        else:
            row = CashForecastDraft.objects.select_for_update().get(
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
