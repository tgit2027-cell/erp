"""Expenses and petty cash: entered by one person, booked once a second person approves (synthetic only).

An expense is a running cost such as rent, fuel or a repair. It is either
paid from one of the unit's bank accounts or cash boxes (a cash box is petty
cash), or paid by a member of staff with their own money and owed back to
them. On approval the books show it at once:

    paid from an account:   Dr expense category   Cr cash and bank (that account)
    paid by staff:          Dr expense category   Cr staff expense claims to repay

and repaying the member of staff later is Dr staff claims, Cr cash and bank.
The approver cannot be the person who entered the expense or the person who
paid it. Rejected expenses are kept with the reason. Categories and rules are
provisional; QAR only, no tax.
"""

import hashlib
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .approval_limits import require_within_limit
from .access import active_grants, holders_filter
from .cash_accounts import _account as _cash_account, _past_day, _reconciled, _text, account_row, can_manage as can_manage_cash
from .general_ledger import _day, can_view_ledger
from .models import (
    CashAccount,
    CashLineAssignment,
    DemoUserMarker,
    Expense,
    JournalEntry,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import (
    InvalidTransaction,
    _begin_idempotent_operation,
    _complete,
    _money,
    _post_journal,
    _require_demo,
)

ZERO = Decimal("0.00")
RECORD = UserActionGrant.Action.RECORD_EXPENSE
APPROVE = UserActionGrant.Action.APPROVE_EXPENSE
CASH_CODE = "TEST_CASH"
CLAIMS_CODE = "TEST_EXPENSE_CLAIMS"
MAX_AMOUNT = Decimal("1000000.00")
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
RECEIPT_TYPES = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}
ACCOUNT_CODES = {category: f"TEST_EXP_{category.upper()}" for category in Expense.Category.values}
POLICY_LABEL = (
    "TEST ONLY provisional rules: a second person approves every expense before it is booked; "
    "categories are a starting list; QAR only, no tax."
)


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_record(user, business_unit):
    return _granted(user, business_unit, RECORD)


def can_approve(user, business_unit):
    return _granted(user, business_unit, APPROVE)


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Expenses run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use expenses.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not (can_record(user, business_unit) or can_approve(user, business_unit) or can_view_ledger(user, business_unit)):
        raise WorkflowPermissionDenied("You need permission to enter, approve or view expenses in this business unit.")


def _row(expense, actor=None):
    return {
        "id": expense.pk, "number": expense.number, "date": expense.expense_date.isoformat(),
        "category": expense.category, "category_label": expense.get_category_display(),
        "description": expense.description, "payee": expense.payee, "amount": format(expense.amount, "f"),
        "paid_from": {"id": expense.paid_from_id, "name": expense.paid_from.name, "kind": expense.paid_from.kind}
        if expense.paid_from_id else None,
        "claimant": {"id": expense.claimant_id, "name": expense.claimant.get_full_name() or expense.claimant.username}
        if expense.claimant_id else None,
        "has_receipt": bool(expense.receipt),
        "receipt_url": f"/api/finance/expenses/{expense.pk}/receipt/" if expense.receipt else "",
        "status": expense.status, "status_label": expense.get_status_display(),
        "submitted_by": expense.submitted_by.username, "submitted_at": expense.submitted_at.isoformat(),
        "decided_by": expense.decided_by.username if expense.decided_by_id else "",
        "decided_at": expense.decided_at.isoformat() if expense.decided_at else "",
        "rejection_reason": expense.rejection_reason,
        "journal_entry_id": expense.journal_entry_id,
        "reimbursed_from": expense.reimbursed_from.name if expense.reimbursed_from_id else "",
        "reimbursed_at": expense.reimbursed_at.isoformat() if expense.reimbursed_at else "",
        **({"can_decide": _may_decide(actor, expense)} if actor is not None else {}),
    }


def _may_decide(actor, expense):
    return (expense.status == Expense.Status.SUBMITTED and can_approve(actor, expense.business_unit)
            and actor.pk not in (expense.submitted_by_id, expense.claimant_id))


def _people(business_unit):
    """People who can be repaid: anyone with a permission in this unit."""
    return get_user_model().objects.filter(holders_filter(business_unit), is_active=True, demo_marker__is_synthetic=True) \
        .distinct().order_by("first_name", "username")


def _month(value):
    if not value:
        return timezone.localdate().replace(day=1)
    day = _day(f"{value}-01" if len(str(value)) == 7 else value, timezone.localdate(), "month")
    return day.replace(day=1)


def expenses_overview(*, actor, business_unit, status="", month=None, search=""):
    _require_user(actor, business_unit)
    start = _month(month)
    end = (start.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)
    expenses = Expense.objects.filter(business_unit=business_unit).select_related(
        "paid_from", "claimant", "submitted_by", "decided_by", "reimbursed_from", "business_unit")
    waiting = expenses.filter(status=Expense.Status.SUBMITTED)
    owed = expenses.filter(status=Expense.Status.APPROVED, claimant__isnull=False)
    listed = expenses.filter(Q(expense_date__gte=start, expense_date__lt=end) | Q(status=Expense.Status.SUBMITTED))
    if status in Expense.Status.values:
        listed = listed.filter(status=status)
    term = (search or "").strip()
    if term:
        listed = listed.filter(Q(number__icontains=term) | Q(description__icontains=term) | Q(payee__icontains=term))
    booked = expenses.filter(expense_date__gte=start, expense_date__lt=end,
                             status__in=[Expense.Status.APPROVED, Expense.Status.REIMBURSED])
    by_category = {row["category"]: row for row in booked.values("category").annotate(total=Sum("amount"), count=Count("pk"))}
    payload = {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "month": start.strftime("%Y-%m"),
        "can_record": can_record(actor, business_unit), "can_approve": can_approve(actor, business_unit),
        "can_reimburse": can_manage_cash(actor, business_unit),
        "rows": [_row(expense, actor) for expense in listed[:300]],
        "totals": {
            "booked": format(booked.aggregate(total=Sum("amount"))["total"] or ZERO, "f"),
            "waiting": waiting.count(), "waiting_amount": format(waiting.aggregate(total=Sum("amount"))["total"] or ZERO, "f"),
            "owed_to_staff": format(owed.aggregate(total=Sum("amount"))["total"] or ZERO, "f"), "owed_count": owed.count(),
        },
        "by_category": [
            {"category": value, "label": label, "total": format(by_category[value]["total"], "f"), "count": by_category[value]["count"]}
            for value, label in Expense.Category.choices if value in by_category
        ],
        "categories": [{"value": value, "label": label} for value, label in Expense.Category.choices],
        "policy_label": POLICY_LABEL,
    }
    if payload["can_record"] or payload["can_reimburse"]:
        payload["accounts"] = [account_row(account) for account in CashAccount.objects.filter(business_unit=business_unit)]
        payload["people"] = [{"id": person.pk, "name": person.get_full_name() or person.username} for person in _people(business_unit)]
    return payload


def _check_receipt(upload):
    name = Path(getattr(upload, "name", "") or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in RECEIPT_TYPES:
        raise InvalidTransaction("Attach the receipt as a PDF, PNG or JPG.")
    size = getattr(upload, "size", 0) or 0
    if size <= 0 or size > MAX_RECEIPT_BYTES:
        raise InvalidTransaction("The receipt must be a file of at most 10 MB.")
    content_type, signatures = RECEIPT_TYPES[suffix]
    upload.seek(0)
    if not any(upload.read(16).startswith(signature) for signature in signatures):
        raise InvalidTransaction("The receipt's content is not the file type its name says.")
    upload.seek(0)
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return content_type, digest.hexdigest()


def _next_number(business_unit, day):
    prefix = f"EXP-{business_unit.code}-{day.year}-"
    last = Expense.objects.select_for_update().filter(number__startswith=prefix).order_by("-number").first()
    following = int(last.number.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{following:05d}"


@transaction.atomic
def submit_expense(*, actor, business_unit, data, receipt=None):
    """Enter an expense for approval. Nothing is booked yet."""
    _require_user(actor, business_unit)
    if not can_record(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'enter expenses' permission for this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the expense's details.")
    day = _past_day(data.get("date"), "date")
    category = data.get("category")
    if category not in Expense.Category.values:
        raise InvalidTransaction("Choose a category.")
    amount = _money(data.get("amount"), "amount")
    if amount <= 0 or amount > MAX_AMOUNT:
        raise InvalidTransaction("The amount must be more than zero and at most QAR 1,000,000.")
    description = _text(data.get("description"), "description", 200, required=True)
    payee = _text(data.get("payee"), "shop or supplier", 120)
    account, claimant = None, None
    paid_by = data.get("paid_by")
    if paid_by == "account":
        account = _cash_account(business_unit, _int(data.get("account_id")), "account it was paid from")
        if day < account.opening_date:
            raise InvalidTransaction(f"{account.name} opened on {account.opening_date.isoformat()}, after this date.")
    elif paid_by == "staff":
        claimant = _people(business_unit).filter(pk=_int(data.get("claimant_id"))).first()
        if claimant is None:
            raise InvalidTransaction("Choose who paid it, from the people working in this unit.")
    else:
        raise InvalidTransaction("Say whether it was paid from a bank account or cash box, or by a member of staff.")
    expense = Expense(
        business_unit=business_unit, number=_next_number(business_unit, day), expense_date=day, category=category,
        description=description, payee=payee, amount=amount, paid_from=account, claimant=claimant, submitted_by=actor,
    )
    if receipt is not None:
        expense.receipt_content_type, expense.receipt_sha256 = _check_receipt(receipt)
        expense.receipt.save(f"receipt{Path(receipt.name).suffix.lower()}", receipt, save=False)
    expense.save()
    return _row(_load(business_unit, expense.pk), actor)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load(business_unit, expense_id, *, lock=False):
    rows = Expense.objects.filter(business_unit=business_unit, pk=expense_id)
    if lock:
        rows = rows.select_for_update(of=("self",))
    expense = rows.select_related("paid_from", "claimant", "submitted_by", "decided_by", "reimbursed_from",
                                  "business_unit__legal_entity").first()
    if expense is None:
        raise InvalidTransaction("That expense was not found.")
    return expense


def _require_decider(actor, expense):
    _require_user(actor, expense.business_unit)
    if not can_approve(actor, expense.business_unit):
        raise WorkflowPermissionDenied("You need the 'approve expenses' permission for this unit.")
    if expense.status != Expense.Status.SUBMITTED:
        raise InvalidTransaction(f"{expense.number} is already {expense.get_status_display().lower()}.")
    if actor.pk == expense.submitted_by_id:
        raise WorkflowPermissionDenied("Someone other than the person who entered the expense must approve it.")
    if actor.pk == expense.claimant_id:
        raise WorkflowPermissionDenied("You cannot approve an expense you are owed for.")


@transaction.atomic
def approve_expense(*, actor, business_unit, expense_id):
    """Approve and book the expense."""
    expense = _load(business_unit, expense_id, lock=True)
    _require_decider(actor, expense)
    require_within_limit(actor, business_unit, APPROVE, expense.amount, "this expense")
    if expense.paid_from_id and _reconciled(expense.paid_from, expense.expense_date):
        raise InvalidTransaction(
            f"{expense.expense_date:%B %Y} is reconciled and approved for {expense.paid_from.name}. "
            "Reject it and enter it with a date in a later month.")
    operation, _ = _begin_idempotent_operation(f"expense-{expense.pk}-approve", "expense_approval", {"expense": expense.pk})
    memo = f"{expense.number} {expense.description}"[:240]
    credit = CASH_CODE if expense.paid_from_id else CLAIMS_CODE
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.EXPENSE, description=f"TEST ONLY: expense {expense.number} · {expense.get_category_display()}",
        lines=[(ACCOUNT_CODES[expense.category], expense.amount, ZERO, memo), (credit, ZERO, expense.amount, memo)],
        document_date=expense.expense_date,
    )
    if expense.paid_from_id:
        cash_line = entry.lines.get(credit__gt=0)
        CashLineAssignment.objects.create(journal_line=cash_line, account=expense.paid_from, assigned_by=actor)
    expense.status, expense.decided_by, expense.decided_at = Expense.Status.APPROVED, actor, timezone.now()
    expense.journal_entry = entry
    expense.save(update_fields=["status", "decided_by", "decided_at", "journal_entry"])
    _complete(operation, {"expense": expense.pk, "journal_entry_id": entry.pk})
    return _row(expense, actor)


@transaction.atomic
def reject_expense(*, actor, business_unit, expense_id, reason):
    expense = _load(business_unit, expense_id, lock=True)
    _require_decider(actor, expense)
    reason = _text(reason, "reason", 200, required=True)
    expense.status, expense.decided_by, expense.decided_at = Expense.Status.REJECTED, actor, timezone.now()
    expense.rejection_reason = reason
    expense.save(update_fields=["status", "decided_by", "decided_at", "rejection_reason"])
    return _row(expense, actor)


@transaction.atomic
def reimburse_expense(*, actor, business_unit, expense_id, account_id, date=None):
    """Repay the member of staff from a bank account or cash box."""
    _require_user(actor, business_unit)
    if not can_manage_cash(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'record bank and cash' permission to repay staff.")
    expense = _load(business_unit, expense_id, lock=True)
    if expense.status != Expense.Status.APPROVED or not expense.claimant_id:
        raise InvalidTransaction("Only approved expenses paid by staff can be repaid.")
    if actor.pk == expense.claimant_id:
        raise WorkflowPermissionDenied("Someone else must repay an expense you are owed for.")
    account = _cash_account(business_unit, _int(account_id), "account it is repaid from")
    day = _past_day(date, "date")
    if day < expense.expense_date:
        raise InvalidTransaction("The repayment cannot be dated before the expense.")
    if day < account.opening_date:
        raise InvalidTransaction(f"{account.name} opened on {account.opening_date.isoformat()}, after this date.")
    if _reconciled(account, day):
        raise InvalidTransaction(f"{day:%B %Y} is reconciled and approved for {account.name}. Use a later date.")
    operation, _ = _begin_idempotent_operation(f"expense-{expense.pk}-repay", "expense_repayment", {"expense": expense.pk})
    name = expense.claimant.get_full_name() or expense.claimant.username
    memo = f"Repaid {name} for {expense.number}"[:240]
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.EXPENSE, description=f"TEST ONLY: {memo}",
        lines=[(CLAIMS_CODE, expense.amount, ZERO, memo), (CASH_CODE, ZERO, expense.amount, memo)], document_date=day,
    )
    CashLineAssignment.objects.create(journal_line=entry.lines.get(credit__gt=0), account=account, assigned_by=actor)
    expense.status, expense.reimbursed_from, expense.reimbursed_by = Expense.Status.REIMBURSED, account, actor
    expense.reimbursed_at, expense.reimbursement_entry = timezone.now(), entry
    expense.save(update_fields=["status", "reimbursed_from", "reimbursed_by", "reimbursed_at", "reimbursement_entry"])
    _complete(operation, {"expense": expense.pk, "journal_entry_id": entry.pk})
    return _row(expense, actor)


def receipt_for_download(*, actor, business_unit, expense_id):
    _require_user(actor, business_unit)
    expense = _load(business_unit, expense_id)
    if not expense.receipt:
        raise InvalidTransaction("This expense has no receipt.")
    return expense
