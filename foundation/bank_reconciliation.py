"""Bank statements matched to our records, and a monthly reconciliation per account (synthetic only).

Statement lines for a bank account or cash box are added from a statement (or
a cash count sheet), one line per movement, positive for money in. Each is
matched to the cash journal line that records the same money on the same
account, one to one, by hand or automatically (same amount, within seven days,
only one candidate). A bank charge or interest line with nothing to match can
be recorded from the statement, which posts it and matches it in one step.

A month is reconciled when every statement line in it is matched, the
statement opening plus its lines equals the statement closing, and the
statement closing equals the account's book balance at month end less money
recorded but not yet on the statement (deposits in transit) plus payments
recorded but not yet cleared. One person with 'record bank and cash' prepares
it; another with 'approve bank reconciliations' approves it. Approval locks
the month's statement lines and matches on that account, and the moves of
cash lines dated in it.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .access import active_grants
from .cash_accounts import (
    _account,
    _cash_lines,
    _past_day,
    _require_manager,
    _require_user,
    _router,
    _text,
    can_manage,
    record_entry,
)
from .models import (
    BankReconciliation,
    BankStatementLine,
    CashAccount,
    CashMatch,
    JournalEntry,
    UserActionGrant,
)
from .document_ai import is_available as document_reader_available
from .document_ai import read_document
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction, _as_decimal, _begin_idempotent_operation, _complete

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
APPROVE = UserActionGrant.Action.APPROVE_BANK_REC
MATCH_WINDOW = datetime.timedelta(days=7)
MAX_LINES = 500


def can_approve(user, business_unit):
    return active_grants(user, APPROVE).filter(business_unit=business_unit).exists()


def _month(value):
    if isinstance(value, str) and len(value) == 7:
        value = f"{value}-01"
    if value in (None, ""):
        value = timezone.localdate().isoformat()
    try:
        day = datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the month as YYYY-MM.") from None
    first = day.replace(day=1)
    last = (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    return first, last


def _signed(value, field):
    number = _as_decimal(value, field)
    if number != number.quantize(CENT):
        raise InvalidTransaction(f"The {field} can have at most two decimals.")
    return number.quantize(CENT)


def _approved_months(account):
    return set(account.reconciliations.filter(status=BankReconciliation.Status.APPROVED).values_list("month", flat=True))


def _require_open(account, day, what):
    if day.replace(day=1) in _approved_months(account):
        raise InvalidTransaction(f"{day:%B %Y} is reconciled and approved for {account.name}, so {what} cannot change.")


def _account_lines(account, end):
    """Cash journal lines that belong to the account, from its opening date to end."""
    route = _router(account.business_unit)
    return [
        line for line in _cash_lines(account.business_unit).filter(
            entry__document_date__gte=account.opening_date, entry__document_date__lte=end,
        ).select_related("cash_match__statement_line", "cash_assignment")
        if route(line) == account.pk
    ]


def _statement_row(line):
    match = getattr(line, "match", None)
    return {
        "id": line.pk, "date": line.line_date.isoformat(), "description": line.description, "reference": line.reference,
        "amount": format(line.amount, "f"),
        "matched_to": match.journal_line_id if match else None,
        "matched_date": match.journal_line.entry.document_date.isoformat() if match else None,
        "matched_description": match.journal_line.entry.description.split(": ", 1)[-1] if match else "",
    }


def _book_row(line):
    return {
        "line_id": line.pk, "date": line.entry.document_date.isoformat(), "journal_entry_id": line.entry_id,
        "description": line.entry.description.split(": ", 1)[-1], "source": line.entry.get_kind_display().replace("Synthetic ", "").capitalize(),
        "amount": format(line.debit - line.credit, "f"),
    }


@transaction.atomic
def add_statement_lines(*, actor, idempotency_key, business_unit, account_id, lines):
    """Add lines from a statement. A line identical to one already added (date, amount, description) is refused."""
    _require_manager(actor, business_unit)
    account = _account(business_unit, account_id)
    if not isinstance(lines, list) or not lines:
        raise InvalidTransaction("Add at least one statement line.")
    if len(lines) > MAX_LINES:
        raise InvalidTransaction(f"Add at most {MAX_LINES} lines at a time.")
    clean = []
    for number, raw in enumerate(lines, start=1):
        if not isinstance(raw, dict):
            raise InvalidTransaction(f"Line {number} is not a statement line.")
        try:
            day = _past_day(raw.get("date"), "date")
            amount = _signed(raw.get("amount"), "amount")
            description = _text(raw.get("description"), "description", 160, required=True)
            reference = _text(raw.get("reference", ""), "reference", 80)
        except InvalidTransaction as error:
            raise InvalidTransaction(f"Line {number}: {error}") from None
        if amount == 0:
            raise InvalidTransaction(f"Line {number}: the amount cannot be zero.")
        if day < account.opening_date:
            raise InvalidTransaction(f"Line {number}: {account.name} opened on {account.opening_date.isoformat()}, after this line.")
        clean.append({"date": day, "amount": amount, "description": description, "reference": reference})
    payload = {"account_id": account.pk, "lines": [{**row, "date": row["date"].isoformat()} for row in clean]}
    operation, created = _begin_idempotent_operation(idempotency_key, "statement_lines", payload)
    if not created:
        return operation.result
    seen = set()
    for number, row in enumerate(clean, start=1):
        _require_open(account, row["date"], "its statement")
        identity = (row["date"], row["amount"], row["description"].lower())
        exists = BankStatementLine.objects.filter(
            account=account, line_date=row["date"], amount=row["amount"], description__iexact=row["description"],
        ).exists()
        if identity in seen or exists:
            raise InvalidTransaction(
                f"Line {number} ({row['date'].isoformat()}, {format(row['amount'], 'f')}) is already on the statement. "
                "Remove it from what you are adding."
            )
        seen.add(identity)
    created_lines = BankStatementLine.objects.bulk_create([
        BankStatementLine(account=account, import_operation=operation, line_date=row["date"], amount=row["amount"],
                          description=row["description"], reference=row["reference"], added_by=actor)
        for row in clean
    ])
    return _complete(operation, {"account_id": account.pk, "added": len(created_lines)})


def _locked_statement_line(business_unit, line_id):
    if type(line_id) is not int:
        raise InvalidTransaction("Choose the statement line.")
    line = BankStatementLine.objects.select_for_update(of=("self",)).select_related("account").filter(
        pk=line_id, account__business_unit=business_unit,
    ).first()
    if line is None:
        raise InvalidTransaction("Statement line not found in this business unit.")
    return line


@transaction.atomic
def remove_statement_line(*, actor, business_unit, line_id):
    _require_manager(actor, business_unit)
    line = _locked_statement_line(business_unit, line_id)
    _require_open(line.account, line.line_date, "its statement")
    if hasattr(line, "match"):
        raise InvalidTransaction("Unmatch this line before removing it.")
    line.delete()
    return {"removed": line_id}


def _match(actor, statement_line, journal_line):
    account = statement_line.account
    if journal_line.debit - journal_line.credit != statement_line.amount:
        raise InvalidTransaction("The amounts differ, so these two cannot be matched.")
    if _router(account.business_unit)(journal_line) != account.pk:
        raise InvalidTransaction(f"That movement is not on {account.name}. Move it to this account first.")
    if journal_line.entry.document_date < account.opening_date:
        raise InvalidTransaction(f"That movement is from before {account.name} opened.")
    # A movement in an approved month can still be matched to a later statement line: it was outstanding then.
    _require_open(account, statement_line.line_date, "its matches")
    return CashMatch.objects.create(statement_line=statement_line, journal_line=journal_line, matched_by=actor)


@transaction.atomic
def match_lines(*, actor, business_unit, statement_line_id, journal_line_id):
    _require_manager(actor, business_unit)
    statement_line = _locked_statement_line(business_unit, statement_line_id)
    if hasattr(statement_line, "match"):
        raise InvalidTransaction("This statement line is already matched.")
    if type(journal_line_id) is not int:
        raise InvalidTransaction("Choose the movement it matches.")
    journal_line = _cash_lines(business_unit).filter(pk=journal_line_id).first()
    if journal_line is None:
        raise InvalidTransaction("Movement not found in this business unit.")
    if CashMatch.objects.filter(journal_line=journal_line).exists():
        raise InvalidTransaction("That movement is already matched to another statement line.")
    _match(actor, statement_line, journal_line)
    return {"statement_line_id": statement_line.pk, "journal_line_id": journal_line.pk}


@transaction.atomic
def unmatch_line(*, actor, business_unit, statement_line_id):
    _require_manager(actor, business_unit)
    statement_line = _locked_statement_line(business_unit, statement_line_id)
    match = getattr(statement_line, "match", None)
    if match is None:
        raise InvalidTransaction("This statement line is not matched.")
    _require_open(statement_line.account, statement_line.line_date, "its matches")
    match.delete()
    return {"statement_line_id": statement_line.pk}


@transaction.atomic
def auto_match(*, actor, business_unit, account_id, month=None):
    """Match each unmatched statement line of the month to the only unmatched movement with its amount within 7 days."""
    _require_manager(actor, business_unit)
    account = _account(business_unit, account_id)
    first, last = _month(month)
    _require_open(account, first, "its matches")
    candidates = [line for line in _account_lines(account, last + MATCH_WINDOW) if not hasattr(line, "cash_match")]
    statement = BankStatementLine.objects.select_for_update(of=("self",)).filter(
        account=account, line_date__range=(first, last), match__isnull=True,
    ).order_by("line_date", "pk")
    matched = 0
    used = set()
    for row in statement:
        fits = [line for line in candidates if line.pk not in used and line.debit - line.credit == row.amount
                and abs(line.entry.document_date - row.line_date) <= MATCH_WINDOW]
        same_amount = [other for other in statement if other.pk != row.pk and other.amount == row.amount]
        if len(fits) == 1 and not same_amount:
            _match(actor, row, fits[0])
            used.add(fits[0].pk)
            matched += 1
    return {"matched": matched}


@transaction.atomic
def record_from_statement(*, actor, idempotency_key, business_unit, statement_line_id, kind):
    """Post a bank charge or interest line straight from the statement and match it."""
    _require_manager(actor, business_unit)
    line = _locked_statement_line(business_unit, statement_line_id)
    if hasattr(line, "match"):
        raise InvalidTransaction("This statement line is already matched.")
    expected = {"bank_charge": -1, "interest": 1}.get(kind)
    if expected is None:
        raise InvalidTransaction("Choose bank charges or interest.")
    if (line.amount > 0) != (expected > 0):
        raise InvalidTransaction("Bank charges are money out and interest is money in; this line is the other way.")
    entry = record_entry(
        actor=actor, idempotency_key=idempotency_key, business_unit=business_unit, kind=kind, account_id=line.account_id,
        amount=format(abs(line.amount), "f"), entry_date=line.line_date.isoformat(), description=line.description,
        reference=line.reference,
    )
    journal = JournalEntry.objects.get(pk=entry["journal_entry_id"])
    cash_line = journal.lines.get(account__code="TEST_CASH")
    if not CashMatch.objects.filter(statement_line=line).exists():
        _match(actor, line, cash_line)
    return {"statement_line_id": line.pk, "entry": entry}


def _figures(account, first, last):
    """Statement and book figures for the month, and what is still open."""
    statement = list(BankStatementLine.objects.filter(account=account, line_date__range=(first, last))
                     .select_related("match__journal_line__entry").order_by("line_date", "pk"))
    book_lines = _account_lines(account, last)
    book_balance = account.opening_balance + sum((line.debit - line.credit for line in book_lines), ZERO)
    outstanding = [
        line for line in book_lines
        if not hasattr(line, "cash_match") or line.cash_match.statement_line.line_date > last
    ]
    unmatched = [row for row in statement if not hasattr(row, "match")]
    late = [row for row in statement if hasattr(row, "match") and row.match.journal_line.entry.document_date > last]
    previous = account.reconciliations.filter(status=BankReconciliation.Status.APPROVED, month__lt=first).order_by("-month").first()
    if previous is not None:
        suggested_opening = previous.statement_closing
    elif first <= account.opening_date <= last:
        suggested_opening = account.opening_balance
    else:
        earlier = BankStatementLine.objects.filter(account=account, line_date__lt=first).aggregate(total=Sum("amount"))["total"]
        suggested_opening = account.opening_balance + (earlier or ZERO)
    return {
        "statement": statement, "movement": sum((row.amount for row in statement), ZERO),
        "book_balance": book_balance, "outstanding": outstanding,
        "outstanding_in": sum((line.debit for line in outstanding), ZERO),
        "outstanding_out": sum((line.credit for line in outstanding), ZERO),
        "unmatched": unmatched, "late": late, "previous": previous, "suggested_opening": suggested_opening,
    }


def _rec_row(rec):
    return {
        "id": rec.pk, "account_id": rec.account_id, "month": rec.month.isoformat()[:7], "status": rec.status,
        "status_label": rec.get_status_display(),
        "statement_opening": format(rec.statement_opening, "f"), "statement_closing": format(rec.statement_closing, "f"),
        "statement_movement": format(rec.statement_movement, "f"), "book_balance": format(rec.book_balance, "f"),
        "outstanding_in": format(rec.outstanding_in, "f"), "outstanding_out": format(rec.outstanding_out, "f"),
        "note": rec.note, "prepared_by": rec.prepared_by.username, "prepared_at": rec.prepared_at.isoformat(),
        "decided_by": rec.decided_by.username if rec.decided_by_id else None, "decision_note": rec.decision_note,
    }


def reconciliation_view(*, actor, business_unit, account_id=None, month=None):
    _require_user(actor, business_unit)
    accounts = list(CashAccount.objects.filter(business_unit=business_unit, is_synthetic=True))
    if account_id is None:
        preferred = sorted(accounts, key=lambda account: (account.kind != CashAccount.Kind.BANK, not account.is_default))
        account = preferred[0] if preferred else None
    else:
        account = _account(business_unit, account_id)
    first, last = _month(month)
    base = {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "accounts": [{"id": each.pk, "name": each.name, "kind": each.kind} for each in accounts],
        "month": first.isoformat()[:7], "month_end": last.isoformat(),
        "can_manage": can_manage(actor, business_unit), "can_approve": can_approve(actor, business_unit),
        "me": actor.username, "account": None, "can_read_documents": document_reader_available(),
    }
    if account is None:
        return base
    figures = _figures(account, first, last)
    unmatched_book = [line for line in figures["outstanding"] if not hasattr(line, "cash_match")]
    suggestions = {}
    for row in figures["unmatched"]:
        fits = [line.pk for line in unmatched_book if line.debit - line.credit == row.amount
                and abs(line.entry.document_date - row.line_date) <= MATCH_WINDOW]
        suggestions[row.pk] = fits
    current = account.reconciliations.filter(month=first).exclude(status=BankReconciliation.Status.REJECTED).first()
    return base | {
        "account": {"id": account.pk, "name": account.name, "kind": account.kind, "kind_label": account.get_kind_display(),
                    "opening_date": account.opening_date.isoformat()},
        "statement": [_statement_row(row) | {"suggested": suggestions.get(row.pk, [])} for row in figures["statement"]],
        "statement_movement": format(figures["movement"], "f"),
        "suggested_opening": format(figures["suggested_opening"], "f"),
        "book_balance": format(figures["book_balance"], "f"),
        "outstanding": [_book_row(line) for line in figures["outstanding"]],
        "outstanding_in": format(figures["outstanding_in"], "f"),
        "outstanding_out": format(figures["outstanding_out"], "f"),
        "expected_closing": format(figures["book_balance"] - figures["outstanding_in"] + figures["outstanding_out"], "f"),
        "unmatched_count": len(figures["unmatched"]),
        "late_count": len(figures["late"]),
        "reconciliation": _rec_row(current) if current else None,
        "history": [_rec_row(rec) for rec in account.reconciliations.select_related("prepared_by", "decided_by")[:12]],
        "locked": first in _approved_months(account),
    }


@transaction.atomic
def prepare_reconciliation(*, actor, idempotency_key, business_unit, account_id, month, statement_opening,
                           statement_closing, note=""):
    _require_manager(actor, business_unit)
    account = _account(business_unit, account_id)
    first, last = _month(month)
    if last >= timezone.localdate():
        raise InvalidTransaction("A month can be reconciled once it has ended.")
    opening = _signed(statement_opening, "statement opening balance")
    closing = _signed(statement_closing, "statement closing balance")
    note = _text(note, "note", 240)
    payload = {"account_id": account.pk, "month": first.isoformat(), "opening": opening, "closing": closing, "note": note}
    operation, created = _begin_idempotent_operation(idempotency_key, "bank_reconciliation", payload)
    if not created:
        return operation.result
    CashAccount.objects.select_for_update().filter(pk=account.pk).first()
    if account.reconciliations.filter(month=first).exclude(status=BankReconciliation.Status.REJECTED).exists():
        raise InvalidTransaction(f"{first:%B %Y} is already reconciled or waiting for approval for {account.name}.")
    earlier_open = account.reconciliations.filter(status=BankReconciliation.Status.PREPARED, month__lt=first).first()
    if earlier_open is not None:
        raise InvalidTransaction(f"{earlier_open.month:%B %Y} is still waiting for approval; approve it first.")
    figures = _figures(account, first, last)
    if figures["previous"] is not None and opening != figures["previous"].statement_closing:
        raise InvalidTransaction(
            f"The opening balance must equal last approved closing balance, {format(figures['previous'].statement_closing, 'f')}."
        )
    if opening + figures["movement"] != closing:
        raise InvalidTransaction(
            f"The statement does not add up: {format(opening, 'f')} opening plus {format(figures['movement'], 'f')} "
            f"of lines is {format(opening + figures['movement'], 'f')}, not {format(closing, 'f')}."
        )
    if figures["unmatched"]:
        raise InvalidTransaction(f"{len(figures['unmatched'])} statement lines are not matched yet.")
    if figures["late"]:
        raise InvalidTransaction(
            f"{len(figures['late'])} statement lines are matched to movements recorded after the month. "
            "Record them in the month, or check the dates."
        )
    expected = figures["book_balance"] - figures["outstanding_in"] + figures["outstanding_out"]
    if expected != closing:
        raise InvalidTransaction(
            f"Our records less what is not on the statement yet give {format(expected, 'f')}, "
            f"which differs from the statement closing {format(closing, 'f')} by {format(closing - expected, 'f')}."
        )
    rec = BankReconciliation.objects.create(
        idempotency=operation, account=account, month=first, statement_opening=opening, statement_closing=closing,
        statement_movement=figures["movement"], book_balance=figures["book_balance"],
        outstanding_in=figures["outstanding_in"], outstanding_out=figures["outstanding_out"], note=note, prepared_by=actor,
    )
    return _complete(operation, _rec_row(rec))


def _locked_rec(actor, business_unit, rec_id):
    if type(rec_id) is not int:
        raise InvalidTransaction("Choose the reconciliation.")
    rec = BankReconciliation.objects.select_for_update().select_related("account").filter(
        pk=rec_id, account__business_unit=business_unit,
    ).first()
    if rec is None:
        raise InvalidTransaction("Reconciliation not found in this business unit.")
    _require_user(actor, business_unit)
    if not can_approve(actor, business_unit):
        raise WorkflowPermissionDenied("You need permission to approve bank reconciliations in this business unit.")
    if rec.status != BankReconciliation.Status.PREPARED:
        raise InvalidTransaction(f"This reconciliation is already {rec.get_status_display().lower()}.")
    if rec.prepared_by_id == actor.pk:
        raise WorkflowPermissionDenied("You prepared this reconciliation, so someone else must decide on it.")
    return rec


@transaction.atomic
def approve_reconciliation(*, actor, business_unit, reconciliation_id):
    rec = _locked_rec(actor, business_unit, reconciliation_id)
    first = rec.month
    last = (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    figures = _figures(rec.account, first, last)
    now = (figures["movement"], figures["book_balance"], figures["outstanding_in"], figures["outstanding_out"])
    then = (rec.statement_movement, rec.book_balance, rec.outstanding_in, rec.outstanding_out)
    if now != then or figures["unmatched"]:
        raise InvalidTransaction("The account changed since this was prepared. Reject it so it can be prepared again.")
    rec.status = BankReconciliation.Status.APPROVED
    rec.decided_by = actor
    rec.decided_at = timezone.now()
    rec.save()
    return _rec_row(rec)


@transaction.atomic
def reject_reconciliation(*, actor, business_unit, reconciliation_id, note):
    rec = _locked_rec(actor, business_unit, reconciliation_id)
    note = _text(note, "reason", 240)
    if not note:
        raise InvalidTransaction("Say why the reconciliation is rejected.")
    rec.status = BankReconciliation.Status.REJECTED
    rec.decided_by = actor
    rec.decided_at = timezone.now()
    rec.decision_note = note
    rec.save()
    return _rec_row(rec)


STATEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "bank_name": {"type": "string"},
        "account_last4": {"type": "string"},
        "currency": {"type": "string"},
        "period_start": {"type": "string"},
        "period_end": {"type": "string"},
        "opening_balance": {"type": "string"},
        "closing_balance": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "description": {"type": "string"},
                    "reference": {"type": "string"},
                    "amount": {"type": "string"},
                },
                "required": ["date", "description", "reference", "amount"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["bank_name", "account_last4", "currency", "period_start", "period_end",
                 "opening_balance", "closing_balance", "lines"],
    "additionalProperties": False,
}

STATEMENT_INSTRUCTIONS = """You are reading a bank statement (or a cash count sheet) for an accounting system.

List every transaction line on it, in the order shown. For each line give:
- date: the transaction (posting) date as YYYY-MM-DD. If the statement shows only day and month, take the year from the statement period. Dates on Qatari statements are usually day first (DD/MM/YYYY).
- description: the narrative as printed, on one line, without the balance.
- reference: the cheque number, transfer reference or bank reference if one is shown, else "".
- amount: a plain number with two decimals and no thousands separators. Positive for money in (credit, deposit), negative for money out (debit, withdrawal, charge).

Do not include opening or closing balance rows, carried forward rows, subtotals or running balances as lines.
Also give the bank name, the last four digits of the account number, the currency code, the statement period start and end (YYYY-MM-DD), and the opening and closing balance as plain numbers (positive unless overdrawn). Use "" for anything the document does not show. Copy what is printed; do not invent or correct figures."""


def _draft_amount(value):
    text = str(value or "").replace(",", "").replace(" ", "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        number = Decimal(text)
    except ArithmeticError:
        return None
    if not number.is_finite() or number == 0:
        return None
    return number.quantize(CENT)


def _draft_day(value):
    try:
        return datetime.date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def read_statement(*, actor, business_unit, account_id, upload, month=None):
    """Read an uploaded statement with AI and return draft lines; nothing is saved.

    Each draft says what needs a look before it is added: a date or amount the
    reader could not make out, a date outside the month or before the account
    opened, or a line already on the statement.
    """
    _require_manager(actor, business_unit)
    account = _account(business_unit, account_id)
    first, last = _month(month) if month else (None, None)
    result = read_document(upload, instructions=STATEMENT_INSTRUCTIONS, schema=STATEMENT_SCHEMA)
    raw_lines = result.get("lines") if isinstance(result, dict) else None
    if not isinstance(raw_lines, list):
        raise InvalidTransaction("The AI reader did not find any lines. Check the file, or add the lines by hand.")
    if len(raw_lines) > MAX_LINES:
        raise InvalidTransaction(f"The statement has more than {MAX_LINES} lines. Split it into smaller files.")
    existing = {
        (row.line_date, row.amount, row.description.lower())
        for row in BankStatementLine.objects.filter(account=account)
    }
    approved = _approved_months(account)
    drafts = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        day = _draft_day(raw.get("date"))
        amount = _draft_amount(raw.get("amount"))
        description = " ".join(str(raw.get("description") or "").split())[:160]
        reference = " ".join(str(raw.get("reference") or "").split())[:80]
        checks = []
        if day is None:
            checks.append("Date not clear")
        elif day > timezone.localdate():
            checks.append("Date is in the future")
        elif day < account.opening_date:
            checks.append("Before the account opened")
        elif day.replace(day=1) in approved:
            checks.append("Month already reconciled")
        elif first and not (first <= day <= last):
            checks.append("Outside this month")
        if amount is None:
            checks.append("Amount not clear")
        if not description:
            checks.append("No description")
        duplicate = bool(day and amount is not None and (day, amount, description.lower()) in existing)
        if duplicate:
            checks.append("Already on the statement")
        drafts.append({
            "date": day.isoformat() if day else str(raw.get("date") or ""),
            "description": description, "reference": reference,
            "amount": format(amount, "f") if amount is not None else str(raw.get("amount") or ""),
            "checks": checks, "duplicate": duplicate,
            # Lines with nothing to check are ticked to add; the rest wait for a person.
            "include": not checks,
        })
    money_in = sum((Decimal(row["amount"]) for row in drafts if row["include"] and Decimal(row["amount"]) > 0), ZERO)
    money_out = sum((-Decimal(row["amount"]) for row in drafts if row["include"] and Decimal(row["amount"]) < 0), ZERO)
    header = {key: str(result.get(key) or "") for key in
              ("bank_name", "account_last4", "currency", "period_start", "period_end", "opening_balance", "closing_balance")}
    warnings = []
    if header["currency"] and header["currency"].upper() != account.currency:
        warnings.append(f"The statement is in {header['currency']}, but {account.name} is in {account.currency}.")
    if header["account_last4"] and account.account_last4 and header["account_last4"][-4:] != account.account_last4:
        warnings.append(f"The statement is for an account ending {header['account_last4'][-4:]}, "
                        f"but {account.name} ends {account.account_last4}.")
    opening, closing = _draft_amount(header["opening_balance"]), _draft_amount(header["closing_balance"])
    every = [Decimal(row["amount"]) for row in drafts if _draft_amount(row["amount"]) is not None]
    if opening is not None and closing is not None and opening + sum(every, ZERO) != closing:
        warnings.append(
            f"The lines read do not add up: opening {format(opening, 'f')} plus the lines is "
            f"{format(opening + sum(every, ZERO), 'f')}, but the statement closes at {format(closing, 'f')}. "
            "A line may be missing or misread."
        )
    return {
        "account_id": account.pk, "file_name": getattr(upload, "name", ""), "statement": header,
        "lines": drafts, "line_count": len(drafts), "ready_count": sum(1 for row in drafts if row["include"]),
        "money_in": format(money_in, "f"), "money_out": format(money_out, "f"), "warnings": warnings,
        "notice": "Read by AI. Check each line against the statement before adding it.",
    }
