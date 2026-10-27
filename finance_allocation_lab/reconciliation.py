"""Exact comparisons of explicit inputs. Never operative bank reconciliation."""
from decimal import Decimal
import re

from django.db import transaction
from foundation.finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _money,
    _require_synthetic_demo, _required_text,
)
from .budget import _date
from .models import ReconciliationProposal, ReconciliationRevision


def _text(value, label, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise FinanceDraftValidationError(f"{label} must be text of at most {maximum} characters.")
    return value  # Preserve supplied source references/explanations, including Arabic.


def _amount(value, label, signed=False):
    pattern = r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?" if signed else r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?"
    if not isinstance(value, str) or not re.fullmatch(pattern, value.strip()):
        raise FinanceDraftValidationError(
            f"{label} must be an exact decimal string with at most two decimal places"
            + ("; a signed balance is allowed." if signed else "; choose an explicit increase/decrease direction."))
    number = Decimal(value.strip())
    magnitude = _money(abs(number))
    return format(-magnitude if number < 0 else magnitude, ".2f")


def _rows(values, side):
    if not isinstance(values, list) or len(values) > 100:
        raise FinanceDraftValidationError(f"Enter at most 100 explicit {side} rows.")
    keys = {"row_id", "date", "amount", "direction", "reference", "explanation"}
    seen, rows = set(), []
    for index, row in enumerate(values, 1):
        if not isinstance(row, dict) or set(row) != keys:
            raise FinanceDraftValidationError(f"{side.title()} row {index}: use only explicit row ID, date, amount, direction, reference and explanation.")
        identifier = row["row_id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier):
            raise FinanceDraftValidationError(f"{side.title()} row {index}: enter a local proposal ID of 1–64 letters/digits/hyphens/underscores; this is not a bank reference format.")
        if identifier in seen:
            raise FinanceDraftValidationError(f"Duplicate {side} row ID {identifier}; source IDs must be unambiguous.")
        seen.add(identifier)
        if row["direction"] not in ("increase", "decrease"):
            raise FinanceDraftValidationError(f"{side.title()} row {identifier}: explicitly choose increase or decrease, not a bank debit/credit convention.")
        rows.append({
            "row_id": identifier, "date": _date(row["date"], f"{side} row {identifier} date").isoformat(),
            "amount": _amount(row["amount"], f"{side.title()} row {identifier} amount"),
            "direction": row["direction"],
            "reference": _text(row["reference"], "Source reference", 200),
            "explanation": _text(row["explanation"], "Source explanation", 2000),
        })
    return rows


def prepare_payload(data, entity):
    _require_synthetic_demo(entity)
    keys = {"name", "currency", "statement_date", "statement_balance", "book_date", "book_balance",
            "explanation", "statement_rows", "book_rows", "matches"}
    if not isinstance(data, dict) or not keys <= set(data) <= keys | {"expected_revision"}:
        raise FinanceDraftValidationError("Enter only explicit reconciliation proposal inputs; no imports, tolerances, approvals or posting fields.")
    if data["currency"] != "QAR":
        raise FinanceDraftValidationError("Only explicit QAR comparisons are supported; no FX is inferred.")
    statement, book = _rows(data["statement_rows"], "statement"), _rows(data["book_rows"], "book")
    if not isinstance(data["matches"], list) or len(data["matches"]) > 100:
        raise FinanceDraftValidationError("Enter at most 100 manual full-row proposed matches.")
    sources = {row["row_id"]: row for row in statement}
    books = {row["row_id"]: row for row in book}
    used_statement, used_book, matches = set(), set(), []
    for match in data["matches"]:
        if not isinstance(match, dict) or set(match) != {"statement_row_id", "book_row_id", "explanation"}:
            raise FinanceDraftValidationError("Each match needs an explicit statement row ID, book row ID and explanation.")
        left, right = match["statement_row_id"], match["book_row_id"]
        if not isinstance(left, str) or not isinstance(right, str) or left not in sources or right not in books:
            raise FinanceDraftValidationError("A proposed match refers to a missing source row; explicitly remove or correct that match.")
        if left in used_statement or right in used_book:
            raise FinanceDraftValidationError("A source row cannot be matched twice; explicitly remove its existing proposal first.")
        if sources[left]["amount"] != books[right]["amount"] or sources[left]["direction"] != books[right]["direction"]:
            raise FinanceDraftValidationError("Incompatible proposed match: this lab supports exact one-to-one full rows with the same amount and increase/decrease direction only. No tolerance or netting is inferred.")
        used_statement.add(left)
        used_book.add(right)
        matches.append({"statement_row_id": left, "book_row_id": right,
                        "explanation": _text(match["explanation"], "Match explanation", 2000)})
    return {
        "name": _required_text(data["name"], "Proposal name", maximum=200), "currency": "QAR",
        "statement_date": _date(data["statement_date"], "statement balance date").isoformat(),
        "statement_balance": _amount(data["statement_balance"], "Statement balance", signed=True),
        "book_date": _date(data["book_date"], "book balance date").isoformat(),
        "book_balance": _amount(data["book_balance"], "Book balance", signed=True),
        "explanation": _text(data["explanation"], "Proposal explanation", 2000),
        "statement_rows": statement, "book_rows": book, "matches": matches,
    }


def review(payload):
    def net(rows):
        return sum((Decimal(row["amount"]) * (1 if row["direction"] == "increase" else -1)
                    for row in rows), Decimal("0.00"))
    left = {match["statement_row_id"] for match in payload["matches"]}
    right = {match["book_row_id"] for match in payload["matches"]}
    unmatched_statement = [row for row in payload["statement_rows"] if row["row_id"] not in left]
    unmatched_book = [row for row in payload["book_rows"] if row["row_id"] not in right]
    difference = Decimal(payload["statement_balance"]) - Decimal(payload["book_balance"])
    unmatched_difference = net(unmatched_statement) - net(unmatched_book)
    warnings = [] if payload["statement_date"] == payload["book_date"] else [{
        "code": "balance_dates_differ",
        "message": "Balance dates differ. Differences are arithmetic comparisons only, not a common-cutoff reconciliation.",
    }]
    return {
        "currency": "QAR", "balance_difference": format(difference, ".2f"),
        "statement_net": format(net(payload["statement_rows"]), ".2f"),
        "book_net": format(net(payload["book_rows"]), ".2f"),
        "unmatched_statement_ids": [row["row_id"] for row in unmatched_statement],
        "unmatched_book_ids": [row["row_id"] for row in unmatched_book],
        "unmatched_statement_net": format(net(unmatched_statement), ".2f"),
        "unmatched_book_net": format(net(unmatched_book), ".2f"),
        "unmatched_row_difference": format(unmatched_difference, ".2f"),
        "comparison_residual": format(difference - unmatched_difference, ".2f"),
        "matched_count": len(payload["matches"]), "warnings": warnings,
        "posted": False, "operational_reconciled": False,
    }


def snapshot(row):
    return {"id": row.pk, "revision": row.revision, **row.payload, "review": review(row.payload)}


def record_revision(row):
    ReconciliationRevision.objects.create(proposal=row, revision=row.revision, snapshot=snapshot(row))


def save_reconciliation(*, data, entity, proposal_id=None):
    payload = prepare_payload(data, entity)
    with transaction.atomic():
        if proposal_id is None:
            row = ReconciliationProposal.objects.create(legal_entity=entity, payload=payload)
        else:
            row = ReconciliationProposal.objects.select_for_update().get(
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
