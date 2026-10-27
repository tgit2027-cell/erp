"""Explicit proposed values, not an operational budget or accounting convention."""
from datetime import date
from decimal import Decimal
import re

from django.db import transaction
from foundation.finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _money,
    _require_synthetic_demo, _required_text,
)
from .services import _lines
from .models import BudgetScenario, BudgetRevision


def _date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise FinanceDraftValidationError(f"Enter {label} explicitly in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FinanceDraftValidationError(f"{label} must be a valid calendar date.") from None


def prepare_payload(data, entity):
    _require_synthetic_demo(entity)
    required = {"name", "start_date", "end_date", "currency", "entries"}
    if not isinstance(data, dict) or not required <= set(data) <= required | {"expected_revision"}:
        raise FinanceDraftValidationError("Enter only explicit scenario name, dates, QAR and entries.")
    name = _required_text(data["name"], "Scenario name", maximum=200)
    start, end = _date(data["start_date"], "scenario start date"), _date(data["end_date"], "scenario end date")
    if end < start:
        raise FinanceDraftValidationError("Scenario end date must not precede its start date.")
    if data["currency"] != "QAR":
        raise FinanceDraftValidationError("Enter QAR explicitly; no other currency or FX is supported.")
    if not isinstance(data["entries"], list) or not 1 <= len(data["entries"]) <= 100:
        raise FinanceDraftValidationError("Enter between 1 and 100 proposed budget rows.")
    entries = []
    for index, entry in enumerate(data["entries"], 1):
        keys = {"start_date", "end_date", "amount", "account_id", "business_unit_id", "dimension_value_ids", "explanation"}
        if not isinstance(entry, dict) or set(entry) != keys:
            raise FinanceDraftValidationError(f"Row {index}: use explicit dates, amount, saved account/BU/dimensions and explanation only.")
        first, last = _date(entry["start_date"], f"row {index} start date"), _date(entry["end_date"], f"row {index} end date")
        if last < first:
            raise FinanceDraftValidationError(f"Row {index}: end date must not precede its start date.")
        if first < start or last > end:
            raise FinanceDraftValidationError(f"Row {index}: dates must lie within the explicitly entered scenario range.")
        if not isinstance(entry["dimension_value_ids"], list) or any(
            isinstance(pk, bool) or not isinstance(pk, int) or pk < 1 for pk in entry["dimension_value_ids"]
        ) or len(entry["dimension_value_ids"]) != len(set(entry["dimension_value_ids"])):
            raise FinanceDraftValidationError(f"Row {index}: select distinct explicit saved dimension IDs.")
        explanation = entry["explanation"]
        if not isinstance(explanation, str) or len(explanation) > 2000:
            raise FinanceDraftValidationError(f"Row {index}: explanation must be text of at most 2,000 characters.")
        amount = _money(entry["amount"])
        # Reuse existing entity/BU/dimension validation without creating a journal.
        resolved = _lines({"title_en": "Explicit proposed budget row", "lines": [{
            "account_id": entry["account_id"], "business_unit_id": entry["business_unit_id"],
            "dimension_value_ids": entry["dimension_value_ids"], "debit": "0.00", "credit": "0.00",
        }]}, entity)[0]
        entries.append({
            "start_date": first.isoformat(), "end_date": last.isoformat(), "amount": format(amount, ".2f"),
            "account_id": resolved["account_id"], "business_unit_id": resolved["business_unit_id"],
            "dimension_value_ids": sorted(resolved["dimension_value_ids"]), "explanation": explanation.strip(),
        })
    return {"name": name, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "currency": "QAR", "entries": entries}


def review(payload):
    rows, warnings = payload["entries"], []
    for i, left in enumerate(rows):
        identity = (left["account_id"], left["business_unit_id"], sorted(left["dimension_value_ids"]))
        for j in range(i + 1, len(rows)):
            right = rows[j]
            if identity != (right["account_id"], right["business_unit_id"], sorted(right["dimension_value_ids"])):
                continue
            if max(left["start_date"], right["start_date"]) > min(left["end_date"], right["end_date"]):
                continue
            duplicate = all(left[key] == right[key] for key in ("start_date", "end_date", "amount"))
            code = "duplicate_entry" if duplicate else "overlapping_dates"
            wording = "duplicate dates/amounts" if duplicate else "overlapping entered date ranges"
            warnings.append({"code": code, "rows": [i + 1, j + 1],
                             "message": f"Rows {i + 1} and {j + 1} have {wording} for the same account/BU/dimensions. "
                                        "Advisory only; each row remains included once in the proposed total. "
                                        "No company acceptance policy is inferred."})
    return {"currency": "QAR",
            "proposed_total": format(sum((Decimal(row["amount"]) for row in rows), Decimal("0.00")), ".2f"),
            "entry_count": len(rows), "warnings": warnings, "posted": False}


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def record_revision(row):
    BudgetRevision.objects.create(scenario=row, revision=row.revision, snapshot=snapshot(row))


def save_scenario(*, data, entity, scenario_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if scenario_id is None:
            row = BudgetScenario.objects.create(legal_entity=entity, payload=payload)
        else:
            row = BudgetScenario.objects.select_for_update().get(
                pk=scenario_id, legal_entity=entity, is_synthetic=True)
            expected = data.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
                raise FinanceDraftValidationError("An integer expected revision is required.")
            if expected != row.revision:
                raise FinanceDraftConflict(row.revision)
            row.payload, row.revision = payload, row.revision + 1
            row.save(update_fields=["payload", "revision", "updated_at"])
        record_revision(row)
        return row
