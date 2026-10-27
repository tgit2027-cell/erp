"""Explicit date proposals only. No calendars, scheduler, recognition or posting."""
import re
from datetime import date
from decimal import Decimal

from .finance_drafts import FinanceDraftValidationError


def validate_proposal(kind, proposal):
    if kind in ("journal", "opening_balance", "accrual_adjustment", "prepayment_adjustment"):
        if proposal not in (None, {}):
            raise FinanceDraftValidationError("Only recurring templates can contain recurring proposal dates.")
        return {}
    if kind != "recurring_template":
        raise FinanceDraftValidationError("Choose a journal draft or recurring template proposal.")
    if not isinstance(proposal, dict) or set(proposal) != {"start", "end", "dates"}:
        raise FinanceDraftValidationError("Supply explicit start, end and dates only; recurrence conventions are not inferred.")

    def parse(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise FinanceDraftValidationError("Use unambiguous YYYY-MM-DD dates only.")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise FinanceDraftValidationError("A proposed date is not a valid calendar date.") from None

    start, end = parse(proposal["start"]), parse(proposal["end"])
    if start > end:
        raise FinanceDraftValidationError("Proposal range start must not be after its end.")
    dates = proposal["dates"]
    if not isinstance(dates, list) or not 1 <= len(dates) <= 100:
        raise FinanceDraftValidationError("Enter 1–100 explicit occurrence dates (technical preview limit).")
    parsed = [parse(value) for value in dates]
    if any(value < start or value > end for value in parsed):
        raise FinanceDraftValidationError("Every explicit date must fall within the entered inclusive proposal range.")
    if any(a >= b for a, b in zip(parsed, parsed[1:])):
        raise FinanceDraftValidationError("Enter distinct occurrence dates in strictly increasing order; no dates are adjusted.")
    return {"start": start.isoformat(), "end": end.isoformat(), "dates": [value.isoformat() for value in parsed]}


def preview_recurring_template(journal):
    from .finance_drafts import finance_draft_snapshot
    if journal.kind != "recurring_template":
        raise FinanceDraftValidationError("Select a saved recurring template proposal, not an ordinary journal.")
    proposal = validate_proposal(journal.kind, journal.recurrence_proposal)
    snapshot = finance_draft_snapshot(journal)
    review = snapshot["review"]
    if not review["balanced"]:
        raise FinanceDraftValidationError("Correct the entry lines to a balanced draft before previewing proposed occurrences.")
    count = len(proposal["dates"])
    return {
        "synthetic_only": True, "posted": False, "scheduled": False,
        "template_id": journal.pk, "revision": journal.revision,
        "occurrence_count": count,
        "occurrences": [{
            "date": value, "debit_total": review["debit_total"],
            "credit_total": review["credit_total"], "balanced": True,
        } for value in proposal["dates"]],
        "entry_lines": snapshot["lines"],
        "total_proposed_debits": format(Decimal(review["debit_total"]) * count, ".2f"),
        "total_proposed_credits": format(Decimal(review["credit_total"]) * count, ".2f"),
    }
