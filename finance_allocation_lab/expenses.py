"""Explicit synthetic expense/petty-cash proposals; no approval or money movement."""
from decimal import Decimal
import re

from django.db import transaction
from foundation.finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _money,
    _require_synthetic_demo, _required_text,
)
from .budget import _date
from .services import _lines
from .models import ExpenseProposal, ExpenseRevision


def _text(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise FinanceDraftValidationError(f"{label}: enter {'nonblank ' if required else ''}text of at most {maximum} characters.")
    return value


def prepare_payload(data, entity):
    _require_synthetic_demo(entity)
    keys = {"name", "proposal_type", "currency", "explanation", "entries"}
    if not isinstance(data, dict) or not keys <= set(data) <= keys | {"expected_revision"}:
        raise FinanceDraftValidationError("Use only explicit expense/petty-cash draft inputs; no payment, approval, tax or posting fields.")
    if data["proposal_type"] not in ("expense", "petty_cash"):
        raise FinanceDraftValidationError("Explicitly choose an expense or petty-cash proposal; neither is approved or paid.")
    if data["currency"] != "QAR":
        raise FinanceDraftValidationError("Only explicit QAR proposals are supported; no FX or tax is inferred.")
    if not isinstance(data["entries"], list) or not 1 <= len(data["entries"]) <= 100:
        raise FinanceDraftValidationError("Enter between 1 and 100 explicit proposal rows.")
    entries = []
    for index, item in enumerate(data["entries"], 1):
        fields = {"expense_date", "party_reference", "description", "amount", "account_id",
                  "business_unit_id", "dimension_value_ids", "receipt_reference"}
        if not isinstance(item, dict) or set(item) != fields:
            raise FinanceDraftValidationError(f"Row {index}: use explicit date, claimant/custodian reference, description, QAR amount, proposed saved account/BU/dimensions and synthetic receipt reference only.")
        dimensions = item["dimension_value_ids"]
        if (not isinstance(dimensions, list) or
            any(isinstance(pk, bool) or not isinstance(pk, int) or pk < 1 for pk in dimensions) or
            len(dimensions) != len(set(dimensions))):
            raise FinanceDraftValidationError(f"Row {index}: choose distinct explicit saved dimension IDs.")
        amount = item["amount"]
        if not isinstance(amount, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", amount.strip()):
            raise FinanceDraftValidationError(f"Row {index}: enter an exact non-negative QAR decimal string with at most two decimal places; no rounding/tax is inferred.")
        amount = _money(amount.strip())
        resolved = _lines({"title_en": "Explicit proposed expense context", "lines": [{
            "account_id": item["account_id"], "business_unit_id": item["business_unit_id"],
            "dimension_value_ids": dimensions, "debit": "0.00", "credit": "0.00",
        }]}, entity)[0]  # Validation only: never save a journal or create a balancing line.
        entries.append({
            "expense_date": _date(item["expense_date"], f"row {index} expense date").isoformat(),
            "party_reference": _text(item["party_reference"], f"Row {index} claimant/custodian reference", 200, required=True),
            "description": _text(item["description"], f"Row {index} description", 2000, required=True),
            "amount": format(amount, ".2f"), "account_id": resolved["account_id"],
            "business_unit_id": resolved["business_unit_id"],
            "dimension_value_ids": sorted(resolved["dimension_value_ids"]),
            "receipt_reference": _text(item["receipt_reference"], f"Row {index} synthetic receipt reference", 200),
        })
    return {"name": _required_text(data["name"], "Proposal name", maximum=200),
            "proposal_type": data["proposal_type"], "currency": "QAR",
            "explanation": _text(data["explanation"], "Proposal explanation", 2000), "entries": entries}


def review(payload):
    return {
        "currency": "QAR",
        "proposed_total": format(sum((Decimal(item["amount"]) for item in payload["entries"]), Decimal("0.00")), ".2f"),
        "entry_count": len(payload["entries"]),
        "missing_receipt_rows": [i for i, row in enumerate(payload["entries"], 1) if not row["receipt_reference"].strip()],
        "unproposed_business_unit_rows": [i for i, row in enumerate(payload["entries"], 1) if row["business_unit_id"] is None],
        "approved": False, "paid": False, "posted": False,
    }


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def record_revision(row):
    ExpenseRevision.objects.create(proposal=row, revision=row.revision, snapshot=snapshot(row))


def save_proposal(*, data, entity, proposal_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if proposal_id is None:
            row = ExpenseProposal.objects.create(legal_entity=entity, payload=payload)
        else:
            row = ExpenseProposal.objects.select_for_update().get(
                pk=proposal_id, legal_entity=entity, is_synthetic=True)
            expected = data.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
                raise FinanceDraftValidationError("An integer expected revision is required.")
            if expected != row.revision:
                raise FinanceDraftConflict(row.revision)
            row.payload, row.revision = payload, row.revision + 1
            row.save(update_fields=["payload", "revision", "updated_at"])
        record_revision(row)
        return row
