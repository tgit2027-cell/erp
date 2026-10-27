"""Explicit source-linked draft comparisons; never generate or post reversals."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from foundation.finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _prepare_lines, _require_synthetic_demo,
    _required_text,
)
from foundation.finance_lab_views import _journal_input
from foundation.models import FinanceDraftJournalRevision
from .budget import _date
from .models import ReversalProposal, ReversalRevision


def source_record(entity, journal_id, revision):
    _require_synthetic_demo(entity)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (journal_id, revision)):
        raise FinanceDraftValidationError("Select an explicit saved journal ID and positive revision.")
    source = FinanceDraftJournalRevision.objects.filter(
        journal_id=journal_id, revision=revision, is_synthetic=True,
        journal__legal_entity=entity, journal__is_synthetic=True).first()
    if source is None:
        raise FinanceDraftValidationError("The specified synthetic source journal revision is unavailable.")
    # Do not invent historical snapshots or fall back to the current journal.
    if (source.snapshot.get("id") != journal_id or source.snapshot.get("revision") != revision
            or source.snapshot.get("legal_entity_id") != entity.pk or source.snapshot.get("currency") != "QAR"):
        raise FinanceDraftValidationError("Source revision metadata is inconsistent; no source was inferred.")
    return source


def prepare(data, entity):
    fields = {"source_journal_id", "source_revision", "proposed_date", "explanation", "lines"}
    if not isinstance(data, dict) or not fields <= set(data) <= fields | {"expected_revision"}:
        raise FinanceDraftValidationError("Supply explicit source revision, date, explanation and lines only; no posting or automatic reversal inputs.")
    source = source_record(entity, data["source_journal_id"], data["source_revision"])
    explanation = _required_text(data["explanation"], "Explanation", maximum=200)
    proposed_date = _date(data["proposed_date"], "proposed date").isoformat()
    raw = data["lines"]
    if not isinstance(raw, list) or len(raw) < 2 or len(raw) > 200:
        raise FinanceDraftValidationError("Enter between two and 200 explicit proposed lines.")
    required = {"account_id", "business_unit_id", "dimension_value_ids", "debit", "credit"}
    for line in raw:
        if not isinstance(line, dict) or not required <= set(line) <= required | {"memo_en"}:
            raise FinanceDraftValidationError("Each line needs explicit account, optional BU, dimension selection and debit/credit strings.")
        if any(not isinstance(line[field], str) or not line[field].strip() for field in ("debit", "credit")):
            raise FinanceDraftValidationError("Enter both debit and credit as exact decimal strings, including an explicit zero.")
        if isinstance(line["account_id"], bool) or not isinstance(line["account_id"], int):
            raise FinanceDraftValidationError("Select an explicit draft account.")
        unit = line["business_unit_id"]
        if unit is not None and (isinstance(unit, bool) or not isinstance(unit, int)):
            raise FinanceDraftValidationError("Choose a saved BU or explicitly none.")
    resolved = _journal_input({"title_en": explanation, "lines": raw}, entity)
    prepared = _prepare_lines(**{key: resolved[key] for key in ("legal_entity", "title_en", "lines")})
    lines = [{
        "account_id": line["account"].pk,
        "business_unit_id": line["business_unit"].pk if line["business_unit"] else None,
        "dimension_value_ids": sorted(value.pk for value in line["dimensions"]),
        "debit": format(line["debit"], ".2f"), "credit": format(line["credit"], ".2f"),
        "memo_en": line["memo_en"],
    } for line in prepared]
    return source, {
        "source_journal_id": source.journal_id, "source_revision": source.revision,
        "proposed_date": proposed_date, "explanation": explanation, "lines": lines,
    }


def review(payload, source):
    lines = payload["lines"]
    debit = sum((Decimal(line["debit"]) for line in lines), Decimal("0.00"))
    credit = sum((Decimal(line["credit"]) for line in lines), Decimal("0.00"))
    issues = []
    for index, line in enumerate(lines, 1):
        if (Decimal(line["debit"]) > 0) == (Decimal(line["credit"]) > 0):
            issues.append(f"Line {index} needs one positive debit or credit, not both or neither.")
    if debit != credit:
        issues.append("Proposed debits and credits do not balance; this is not a balanced review.")
    nets = defaultdict(lambda: Decimal("0.00"))
    for line in source["lines"]:
        key = (line["account_id"], line["business_unit_id"], tuple(sorted(value["id"] for value in line["dimensions"])))
        nets[key] += Decimal(line["debit"]) - Decimal(line["credit"])
    for line in lines:
        key = (line["account_id"], line["business_unit_id"], tuple(line["dimension_value_ids"]))
        nets[key] += Decimal(line["debit"]) - Decimal(line["credit"])
    differences = [{
        "account_id": key[0], "business_unit_id": key[1], "dimension_value_ids": list(key[2]),
        "combined_net": format(nets[key], ".2f"),
    } for key in sorted(nets, key=lambda key: (key[0], key[1] or 0, key[2])) if nets[key] != 0]
    return {
        "currency": "QAR", "debit_total": format(debit, ".2f"), "credit_total": format(credit, ".2f"),
        "difference": format(debit - credit, ".2f"), "balanced": not issues, "issues": issues,
        "offsets_source": not issues and source["review"]["balanced"] and not differences,
        "comparison": "Arithmetic net comparison by account, BU and dimensions only; not an approved reversal.",
        "bucket_differences": differences, "proposed": True, "posted": False, "approved": False,
    }


def snapshot(row):
    source = row.source_record.snapshot
    return {"id": row.pk, "revision": row.revision, **row.payload,
            "source": source, "review": review(row.payload, source)}


def save_reversal(*, data, entity, draft_id=None):
    with transaction.atomic():
        source, payload = prepare(data, entity)
        if draft_id is None:
            if "expected_revision" in data:
                raise FinanceDraftValidationError("Expected revision is for edits only.")
            row = ReversalProposal.objects.create(legal_entity=entity, source_record=source, payload=payload)
        else:
            row = ReversalProposal.objects.select_for_update().get(
                pk=draft_id, legal_entity=entity, is_synthetic=True)
            expected = data.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
                raise FinanceDraftValidationError("Supply the positive expected revision.")
            if row.revision != expected:
                raise FinanceDraftConflict(row.revision)
            row.payload, row.source_record, row.revision = payload, source, row.revision + 1
            row.save(update_fields=["payload", "source_record", "revision", "updated_at"])
        ReversalRevision.objects.create(
            draft=row, source_record=source, revision=row.revision, snapshot=snapshot(row))
        return row
