"""Explicit proposal arithmetic only. No operational journal or posting is created."""
from datetime import date
from decimal import Decimal
import re

from django.db import transaction
from foundation.finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _money, _prepare_lines,
    _require_synthetic_demo, _required_text,
)
from foundation.finance_lab_views import _journal_input
from .models import AllocationProposal, AllocationRevision


def _lines(data, entity):
    raw_lines = data.get("lines")
    if isinstance(raw_lines, list):
        for line in raw_lines:
            if isinstance(line, dict):
                for key in ("account_id", "business_unit_id"):
                    value = line.get(key)
                    if key == "business_unit_id" and value is None:
                        continue
                    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                        raise FinanceDraftValidationError("Select explicit saved account/BU IDs; ambiguous IDs are not accepted.")
    inputs = _journal_input(data, entity)
    # Allocation sheets cannot quietly carry recurrence/adjustment/opening metadata.
    if (inputs["kind"] != "journal" or inputs["recurrence_proposal"] not in (None, {})
            or inputs["adjustment_proposal"] not in (None, {}) or inputs["as_of_date"] not in (None, "")):
        raise FinanceDraftValidationError("Allocation proposals accept only explicitly entered journal lines.")
    prepared = _prepare_lines(
        legal_entity=entity, title_en=inputs["title_en"], title_ar=inputs["title_ar"], lines=inputs["lines"])
    return [{
        "account_id": line["account"].pk,
        "business_unit_id": line["business_unit"].pk if line["business_unit"] else None,
        "dimension_value_ids": [value.pk for value in line["dimensions"]],
        "debit": format(line["debit"], ".2f"), "credit": format(line["credit"], ".2f"),
        "memo_en": line["memo_en"], "memo_ar": line["memo_ar"],
    } for line in prepared]


def prepare_payload(data, entity):
    _require_synthetic_demo(entity)
    if not isinstance(data, dict) or not set(data) <= {
        "title_en", "title_ar", "lines", "allocation_proposal", "expected_revision"
    }:
        raise FinanceDraftValidationError("Only explicit allocation proposal and journal draft inputs are accepted.")
    metadata = data.get("allocation_proposal")
    if not isinstance(metadata, dict) or set(metadata) != {
        "source_amount", "currency", "date", "explanation", "destinations"
    }:
        raise FinanceDraftValidationError("Enter explicit source amount, currency, date, explanation and destinations only.")
    if metadata["currency"] != "QAR":
        raise FinanceDraftValidationError("Select QAR explicitly; this isolated lab has no other currency or FX support.")
    source = _money(metadata["source_amount"])
    if source <= 0:
        raise FinanceDraftValidationError("Source amount must be positive.")
    value = metadata["date"]
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise FinanceDraftValidationError("Enter an explicit proposed date in YYYY-MM-DD format.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise FinanceDraftValidationError("The proposed date must be a valid calendar date.") from None
    explanation = _required_text(metadata["explanation"], "Allocation explanation", maximum=2000)
    destinations = metadata["destinations"]
    if not isinstance(destinations, list) or not 1 <= len(destinations) <= 100:
        raise FinanceDraftValidationError("Enter between 1 and 100 explicit destinations.")
    resolved, total = [], Decimal("0.00")
    for item in destinations:
        if not isinstance(item, dict) or not set(item) <= {
            "amount", "account_id", "business_unit_id", "dimension_value_ids"
        }:
            raise FinanceDraftValidationError("Destination inputs must be explicit amounts, accounts, BU and dimensions only.")
        amount = _money(item.get("amount"))
        if amount <= 0:
            raise FinanceDraftValidationError("Each destination amount must be positive.")
        line = _lines({"title_en": "Explicit destination", "lines": [{
            **{key: val for key, val in item.items() if key != "amount"},
            "debit": format(amount, ".2f"), "credit": "0.00",
        }]}, entity)[0]
        resolved.append({key: line[key] for key in ("account_id", "business_unit_id", "dimension_value_ids")} | {
            "amount": format(amount, ".2f")})
        total += amount
    if total > source:
        raise FinanceDraftValidationError(
            f"Overallocated by {total - source:.2f} QAR; reduce explicit destination amounts. No rounding is distributed.")
    lines = _lines(data, entity)
    return {
        "title_en": _required_text(data.get("title_en"), "English allocation title"),
        "title_ar": data.get("title_ar", "").strip(),
        "lines": lines,
        "allocation_proposal": {
            "source_amount": format(source, ".2f"), "currency": "QAR", "date": value,
            "explanation": explanation, "destinations": resolved,
        },
    }


def review(payload):
    meta = payload["allocation_proposal"]
    source = Decimal(meta["source_amount"])
    allocated = sum((Decimal(row["amount"]) for row in meta["destinations"]), Decimal("0.00"))
    lines = payload["lines"]
    debit = sum((Decimal(row["debit"]) for row in lines), Decimal("0.00"))
    credit = sum((Decimal(row["credit"]) for row in lines), Decimal("0.00"))
    issues = []
    if len(lines) < 2:
        issues.append({"code": "at_least_two_lines", "en": "Add at least two lines before review."})
    for index, row in enumerate(lines, 1):
        if (Decimal(row["debit"]) > 0) == (Decimal(row["credit"]) > 0):
            issues.append({"code": f"line_{index}_one_side_only", "en": f"Line {index} must have a positive debit or credit, not both."})
    if debit != credit:
        issues.append({"code": "totals_not_balanced", "en": "Draft totals do not balance; correct the draft before review."})
    return {
        "source_amount": format(source, ".2f"), "allocated_total": format(allocated, ".2f"),
        "difference": format(source - allocated, ".2f"), "currency": "QAR",
        "state": "fully_allocated" if allocated == source else "remaining",
        "journal": {"debit_total": format(debit, ".2f"), "credit_total": format(credit, ".2f"),
                    "difference": format(debit-credit, ".2f"), "balanced": not issues,
                    "posted": False, "issues": issues},
    }


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def record_revision(row):
    AllocationRevision.objects.create(proposal=row, revision=row.revision, snapshot=snapshot(row))


def save_proposal(*, data, entity, proposal_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if proposal_id is None:
            row = AllocationProposal.objects.create(legal_entity=entity, payload=payload)
        else:
            row = AllocationProposal.objects.select_for_update().get(pk=proposal_id, legal_entity=entity, is_synthetic=True)
            expected = data.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
                raise FinanceDraftValidationError("An integer expected revision is required.")
            if expected != row.revision:
                raise FinanceDraftConflict(row.revision)
            row.payload, row.revision = payload, row.revision + 1
            row.save(update_fields=["payload", "revision", "updated_at"])
        record_revision(row)
        return row
