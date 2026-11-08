"""Manual journal entries: corrections, accruals and prepayments typed in by Finance.

Provisional rules (synthetic demo only, waiting for Finance's approval):

- One person prepares a journal and a different person approves it. Nothing
  reaches the accounts until it is approved.
- The lines must balance (total debits equal total credits) in QAR, and there
  must be at least two of them.
- Accounts kept right by their own screens (stock, bank and cash, what
  customers and suppliers owe, deposits, staff claims, fixed assets) cannot be
  used here, so those screens and the accounts always agree.
- An accrual can be set to undo itself on the first day of the next month.
- A posted journal is never changed. To undo it, "Reverse" prepares a new
  journal with the sides swapped, which is approved like any other.
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .approval_limits import require_within_limit
from .access import active_grants
from .cash_accounts import _past_day, _text
from .general_ledger import _day, can_view_ledger
from .models import (
    ClosedMonth, DemoUserMarker, JournalEntry, ManualJournal, ManualJournalEvent, ManualJournalLine, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import (
    _TEST_ACCOUNTS, InvalidTransaction, _begin_idempotent_operation, _complete, _money, _post_journal, _require_demo,
)

ZERO = Decimal("0.00")
PREPARE = UserActionGrant.Action.PREPARE_JOURNAL
APPROVE = UserActionGrant.Action.APPROVE_JOURNAL
MAX_LINES = 40
MAX_AMOUNT = Decimal("100000000.00")
# Kept right by their own screens; a manual journal on them would make that screen and the accounts disagree.
CONTROL_ACCOUNTS = {
    "TEST_INVENTORY": "Stock (changed by receipts, deliveries and stock counts)",
    "TEST_GRNI": "Goods received not yet billed (changed by receipts and supplier bills)",
    "TEST_LANDED_CLEARING": "Shipping and clearance charges (changed by receipts)",
    "TEST_RECEIVABLE": "What customers owe (changed by invoices and payments)",
    "TEST_CASH": "Bank and cash (changed under Bank and cash)",
    "TEST_CUSTOMER_DEPOSITS": "Customer deposits (changed by quote payments)",
    "TEST_SUPPLIER_ADVANCES": "Advances paid to suppliers (changed under Supplier advances)",
    "TEST_EXPENSE_CLAIMS": "Staff expense claims (changed under Expenses)",
    "TEST_FIXED_ASSETS": "Fixed assets (changed under Fixed assets)",
    "TEST_ACCUM_DEPRECIATION": "Accumulated depreciation (changed under Fixed assets)",
    "TEST_UNIT_SHARING": "Costs shared between business units (changed under Cost sharing)",
    "TEST_SALARIES_PAYABLE": "Salaries to pay (changed by payroll)",
    "TEST_COMMISSION_PAYABLE": "Commission and rewards to pay (changed by commission and rewards, and payroll)",
    "TEST_STAFF_LOANS": "Staff loans and advances (changed under Staff loans and by payroll)",
    "TEST_WIP": "Factory jobs in production (changed under Factory)",
    "TEST_FINISHED_JOBS": "Finished factory jobs not yet received (changed under Factory)",
    "TEST_PROJECT_RECEIVABLE": "Project clients to collect (changed under Projects & Fit-Out)",
    "TEST_RETENTION_RECEIVABLE": "Retention held by project clients (changed under Projects & Fit-Out)",
    "TEST_PROJECT_ADVANCES": "Advances from project clients (changed under Projects & Fit-Out)",
    "TEST_OPENING_BALANCES": "Opening balances (changed by the go-live imports)",
}
POLICY_LABEL = ("TEST ONLY provisional rules: a second person approves every manual journal; "
                "accounts kept by their own screens are excluded; QAR only.")


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_prepare(user, business_unit):
    return _granted(user, business_unit, PREPARE)


def can_approve(user, business_unit):
    return _granted(user, business_unit, APPROVE)


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Journal entries run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use journal entries.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not (can_prepare(user, business_unit) or can_approve(user, business_unit) or can_view_ledger(user, business_unit)):
        raise WorkflowPermissionDenied("You need permission to prepare, approve or view journal entries in this business unit.")


def accounts():
    """The accounts a manual journal may use, by kind."""
    order = {"asset": 0, "liability": 1, "revenue": 2, "expense": 3}
    rows = [{"code": code, "name": name.replace("TEST ONLY — ", ""), "kind": str(kind)}
            for code, (name, kind) in _TEST_ACCOUNTS.items() if code not in CONTROL_ACCOUNTS]
    return sorted(rows, key=lambda row: (order.get(row["kind"], 9), row["name"]))


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _totals(lines):
    debit = sum((line.debit for line in lines), ZERO)
    credit = sum((line.credit for line in lines), ZERO)
    return debit, credit


def _row(journal, actor=None, *, with_lines=False):
    lines = list(journal.lines.all())
    debit, credit = _totals(lines)
    names = {code: name.replace("TEST ONLY — ", "") for code, (name, _) in _TEST_ACCOUNTS.items()}
    row = {
        "id": journal.pk, "number": journal.number, "date": journal.entry_date.isoformat(),
        "description": journal.description, "status": journal.status, "status_label": journal.get_status_display(),
        "reverse_next_month": journal.reverse_next_month, "total": format(debit, "f"),
        "balanced": debit == credit and debit > 0, "difference": format(debit - credit, "f"),
        "prepared_by": _name(journal.prepared_by), "prepared_at": journal.prepared_at.isoformat(),
        "returned_reason": journal.returned_reason,
        "approved_by": _name(journal.approved_by), "approved_at": journal.approved_at.isoformat() if journal.approved_at else "",
        "journal_entry_id": journal.journal_entry_id, "auto_reversal_entry_id": journal.auto_reversal_entry_id,
        "reversal_of": journal.reversal_of.number if journal.reversal_of_id else "",
        "reversed_by": journal.reversed_by.number if hasattr(journal, "reversed_by") else "",
    }
    if with_lines:
        row["lines"] = [{"account_code": line.account_code, "account": names.get(line.account_code, line.account_code),
                         "debit": format(line.debit, "f"), "credit": format(line.credit, "f"), "memo": line.memo}
                        for line in lines]
        row["history"] = [{"at": event.at.isoformat(), "by": _name(event.actor), "change": event.change, "note": event.note}
                          for event in journal.events.select_related("actor")[:30]]
    if actor is not None:
        mine = journal.prepared_by_id == actor.pk
        row["can_edit"] = journal.status == ManualJournal.Status.DRAFT and mine and can_prepare(actor, journal.business_unit)
        row["can_decide"] = (journal.status == ManualJournal.Status.SUBMITTED and not mine
                             and can_approve(actor, journal.business_unit))
        row["can_reverse"] = (journal.status == ManualJournal.Status.POSTED and not hasattr(journal, "reversed_by")
                              and not journal.reverse_next_month and journal.reversal_of_id is None
                              and can_prepare(actor, journal.business_unit))
    return row


def _month(value):
    if not value:
        return None
    day = _day(f"{value}-01" if len(str(value)) == 7 else value, timezone.localdate(), "month")
    return day.replace(day=1)


def journals_overview(*, actor, business_unit, status="", month=None, search=""):
    _require_user(actor, business_unit)
    rows = ManualJournal.objects.filter(business_unit=business_unit).select_related(
        "prepared_by", "approved_by", "reversal_of", "business_unit").prefetch_related("lines")
    if status in ManualJournal.Status.values:
        rows = rows.filter(status=status)
    start = _month(month)
    if start:
        end = (start.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)
        rows = rows.filter(entry_date__gte=start, entry_date__lt=end)
    term = " ".join((search or "").split())[:80]
    if term:
        rows = rows.filter(Q(number__icontains=term) | Q(description__icontains=term)
                           | Q(lines__memo__icontains=term)).distinct()
    waiting = ManualJournal.objects.filter(business_unit=business_unit, status=ManualJournal.Status.SUBMITTED)
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "journals": [_row(journal, actor) for journal in rows[:200]],
        "waiting": waiting.count(),
        "waiting_for_me": waiting.exclude(prepared_by=actor).count() if can_approve(actor, business_unit) else 0,
        "accounts": accounts(),
        "control_accounts": [label for label in CONTROL_ACCOUNTS.values()],
        "can_prepare": can_prepare(actor, business_unit), "can_approve": can_approve(actor, business_unit),
        "policy": POLICY_LABEL,
    }


def _load(business_unit, journal_id, *, lock=False):
    rows = ManualJournal.objects.filter(business_unit=business_unit, pk=journal_id if type(journal_id) is int else None)
    if lock:
        rows = rows.select_for_update(of=("self",))
    journal = rows.select_related("prepared_by", "approved_by", "reversal_of", "business_unit__legal_entity").first()
    if journal is None:
        raise InvalidTransaction("That journal was not found.")
    return journal


def journal_detail(*, actor, business_unit, journal_id):
    _require_user(actor, business_unit)
    return _row(_load(business_unit, journal_id), actor, with_lines=True)


def _event(journal, actor, change, note=""):
    ManualJournalEvent.objects.create(journal=journal, actor=actor, change=change, note=note[:200])


def _clean_lines(raw):
    if not isinstance(raw, list):
        raise InvalidTransaction("Send the journal's lines.")
    if len(raw) > MAX_LINES:
        raise InvalidTransaction(f"A journal can have up to {MAX_LINES} lines.")
    allowed = {row["code"] for row in accounts()}
    lines = []
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise InvalidTransaction(f"Line {number} is not readable.")
        debit = _money(item.get("debit") or "0", f"line {number} debit")
        credit = _money(item.get("credit") or "0", f"line {number} credit")
        code = item.get("account_code")
        if not code and debit == 0 and credit == 0:
            continue  # an empty row left in the grid
        if code in CONTROL_ACCOUNTS:
            raise InvalidTransaction(f"Line {number}: {CONTROL_ACCOUNTS[code]} cannot be used in a manual journal.")
        if code not in allowed:
            raise InvalidTransaction(f"Line {number}: choose an account.")
        if (debit > 0) == (credit > 0) or debit < 0 or credit < 0:
            raise InvalidTransaction(f"Line {number}: put an amount in either debit or credit, not both.")
        if max(debit, credit) > MAX_AMOUNT:
            raise InvalidTransaction(f"Line {number}: the amount is too large.")
        lines.append(ManualJournalLine(account_code=code, debit=debit, credit=credit,
                                       memo=_text(item.get("memo"), f"line {number} note", 120)))
    return lines


def _check_open(business_unit, day):
    if ClosedMonth.objects.filter(business_unit=business_unit, month=day.replace(day=1)).exists():
        raise InvalidTransaction(f"{day:%B %Y} is closed for {business_unit.name}. Use a date in an open month.")


def _next_number(business_unit, day):
    prefix = f"MJ-{business_unit.code}-{day.year}-"
    last = ManualJournal.objects.select_for_update().filter(number__startswith=prefix).order_by("-number").first()
    following = int(last.number.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{following:05d}"


@transaction.atomic
def save_journal(*, actor, business_unit, data, journal_id=None):
    """Create or change a draft. With submit=true it is also sent for approval, which needs balanced lines."""
    _require_user(actor, business_unit)
    if not can_prepare(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'prepare journal entries' permission for this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the journal's details.")
    day = _past_day(data.get("date"), "date")
    _check_open(business_unit, day)
    description = _text(data.get("description"), "description", 200, required=True)
    lines = _clean_lines(data.get("lines"))
    reverse_next_month = data.get("reverse_next_month") is True
    if journal_id is None:
        journal = ManualJournal(business_unit=business_unit, number=_next_number(business_unit, day), prepared_by=actor)
        change = "prepared"
    else:
        journal = _load(business_unit, journal_id, lock=True)
        if journal.status != ManualJournal.Status.DRAFT:
            raise InvalidTransaction(f"{journal.number} is {journal.get_status_display().lower()} and can no longer be changed.")
        if journal.prepared_by_id != actor.pk:
            raise WorkflowPermissionDenied("Only the person who prepared this journal can change it.")
        if journal.reversal_of_id and lines:
            # A reversal keeps the swapped lines of the journal it undoes.
            lines = []
        change = "changed"
    journal.entry_date, journal.description, journal.reverse_next_month = day, description, reverse_next_month
    if journal.reversal_of_id:
        journal.reverse_next_month = False
    journal.save()
    if lines or not journal.reversal_of_id:
        journal.lines.all().delete()
        for line in lines:
            line.journal = journal
        ManualJournalLine.objects.bulk_create(lines)
    if change == "prepared" or data.get("submit") is not True:
        _event(journal, actor, change)
    if data.get("submit") is True:
        return _submit(actor, journal)
    return _row(_load(business_unit, journal.pk), actor, with_lines=True)


def _submit(actor, journal):
    lines = list(journal.lines.all())
    debit, credit = _totals(lines)
    if len(lines) < 2:
        raise InvalidTransaction("A journal needs at least two lines.")
    if debit != credit:
        raise InvalidTransaction(f"The journal does not balance: debits are {debit:,.2f} and credits are {credit:,.2f}.")
    journal.status, journal.submitted_at, journal.returned_reason = ManualJournal.Status.SUBMITTED, timezone.now(), ""
    journal.save(update_fields=["status", "submitted_at", "returned_reason"])
    _event(journal, actor, "sent for approval")
    return _row(_load(journal.business_unit, journal.pk), actor, with_lines=True)


def _require_decider(actor, journal):
    _require_user(actor, journal.business_unit)
    if not can_approve(actor, journal.business_unit):
        raise WorkflowPermissionDenied("You need the 'approve journal entries' permission for this unit.")
    if journal.status != ManualJournal.Status.SUBMITTED:
        raise InvalidTransaction(f"{journal.number} is {journal.get_status_display().lower()}, not waiting for approval.")
    if actor.pk == journal.prepared_by_id:
        raise WorkflowPermissionDenied("Someone other than the person who prepared this journal must approve it.")


def _first_of_next_month(day):
    return (day.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)


@transaction.atomic
def decide_journal(*, actor, business_unit, journal_id, action, reason=""):
    journal = _load(business_unit, journal_id, lock=True)
    if action == "withdraw":
        if journal.prepared_by_id != actor.pk or journal.status != ManualJournal.Status.SUBMITTED:
            raise InvalidTransaction("Only the person who prepared a journal can take it back, while it waits for approval.")
        journal.status = ManualJournal.Status.DRAFT
        journal.save(update_fields=["status"])
        _event(journal, actor, "taken back")
        return _row(journal, actor, with_lines=True)
    if action == "submit":
        if journal.prepared_by_id != actor.pk or journal.status != ManualJournal.Status.DRAFT:
            raise InvalidTransaction("Only the person who prepared a draft can send it for approval.")
        _check_open(business_unit, journal.entry_date)
        return _submit(actor, journal)
    _require_decider(actor, journal)
    if action == "return":
        reason = _text(reason, "reason", 200, required=True)
        journal.status, journal.returned_reason = ManualJournal.Status.DRAFT, reason
        journal.save(update_fields=["status", "returned_reason"])
        _event(journal, actor, "sent back", reason)
        return _row(journal, actor, with_lines=True)
    if action != "approve":
        raise InvalidTransaction("Approve or send back.")
    lines = list(journal.lines.all())
    require_within_limit(actor, business_unit, APPROVE, sum((line.debit for line in lines), Decimal("0.00")), "this journal")
    rows = [(line.account_code, line.debit, line.credit, f"{journal.number} {line.memo or journal.description}"[:200])
            for line in lines]
    operation, _ = _begin_idempotent_operation(f"manual-journal-{journal.pk}-post", "manual_journal", {"journal": journal.pk})
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.MANUAL, description=f"TEST ONLY: {journal.number} {journal.description}"[:240],
        lines=rows, document_date=journal.entry_date,
    )
    _complete(operation, {"journal": journal.pk, "journal_entry_id": entry.pk})
    journal.status, journal.approved_by, journal.approved_at = ManualJournal.Status.POSTED, actor, timezone.now()
    journal.journal_entry = entry
    if journal.reverse_next_month:
        day = _first_of_next_month(journal.entry_date)
        undo, _ = _begin_idempotent_operation(f"manual-journal-{journal.pk}-auto-reverse", "manual_journal",
                                              {"journal": journal.pk})
        journal.auto_reversal_entry = _post_journal(
            legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=undo,
            kind=JournalEntry.Kind.MANUAL, description=f"TEST ONLY: {journal.number} undone on {day:%d %b %Y}"[:240],
            lines=[(code, credit, debit, f"Undo {memo}"[:200]) for code, debit, credit, memo in rows], document_date=day,
        )
        _complete(undo, {"journal": journal.pk, "journal_entry_id": journal.auto_reversal_entry.pk})
    journal.save(update_fields=["status", "approved_by", "approved_at", "journal_entry", "auto_reversal_entry"])
    _event(journal, actor, "approved and posted",
           f"undone on {_first_of_next_month(journal.entry_date):%d %b %Y}" if journal.reverse_next_month else "")
    return _row(journal, actor, with_lines=True)


@transaction.atomic
def reverse_journal(*, actor, business_unit, journal_id, date=None):
    """Prepare a new draft with the sides swapped. It is approved like any other journal."""
    _require_user(actor, business_unit)
    if not can_prepare(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'prepare journal entries' permission for this unit.")
    original = _load(business_unit, journal_id, lock=True)
    if original.status != ManualJournal.Status.POSTED:
        raise InvalidTransaction("Only a posted journal can be reversed.")
    if hasattr(original, "reversed_by"):
        raise InvalidTransaction(f"{original.number} is already reversed by {original.reversed_by.number}.")
    if original.reverse_next_month or original.reversal_of_id:
        raise InvalidTransaction(f"{original.number} already undoes itself or another journal.")
    day = _past_day(date or timezone.localdate().isoformat(), "date")
    if day < original.entry_date:
        raise InvalidTransaction("The reversal cannot be dated before the journal it undoes.")
    _check_open(business_unit, day)
    journal = ManualJournal.objects.create(
        business_unit=business_unit, number=_next_number(business_unit, day), entry_date=day,
        description=f"Reverses {original.number}: {original.description}"[:200], prepared_by=actor, reversal_of=original,
    )
    ManualJournalLine.objects.bulk_create([
        ManualJournalLine(journal=journal, account_code=line.account_code, debit=line.credit, credit=line.debit,
                          memo=line.memo) for line in original.lines.all()])
    _event(journal, actor, "prepared", f"reverses {original.number}")
    return _row(_load(business_unit, journal.pk), actor, with_lines=True)

