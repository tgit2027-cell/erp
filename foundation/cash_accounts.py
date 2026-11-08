"""Bank accounts and cash boxes: money in and out per account (synthetic only).

Every posted journal line on cash and bank (TEST_CASH) belongs to one account of
its business unit. Entries recorded here (transfers, bank charges, interest)
say which. Money from other screens goes, until someone moves it, to the
unit's default cash box when it was taken or refunded in cash, and to its
default bank account otherwise (card, transfer, supplier payments). A line
dated before an account's opening date is not part of that account.

Each unit has one cash account and any number of bank accounts. One bank can
hold several accounts (current, savings, LTR, LG), shown together under the
bank's name (Bilal, 1 Nov 2026). LTR and LG accounts are facilities, not money
available, so they are shown apart and left out of the available total.

Viewing needs 'view general ledger' or 'record bank and cash' in the unit;
adding accounts, recording entries and moving lines need 'record bank and
cash'. Accounts and rules are provisional; QAR only.
"""

import re
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from .access import active_grants
from .general_ledger import _day, can_view_ledger
from .models import (
    BankReconciliation,
    CashAccount,
    CashEntry,
    CashLineAssignment,
    CustomerCreditNote,
    DemoUserMarker,
    JournalEntry,
    JournalLine,
    QuotePayment,
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
FACILITIES = {CashAccount.AccountType.LTR, CashAccount.AccountType.LG}
TYPE_ORDER = {value: index for index, value in enumerate(CashAccount.AccountType.values)}
MANAGE = UserActionGrant.Action.MANAGE_CASH
CASH_CODE = "TEST_CASH"
POLICY_LABEL = (
    "TEST ONLY: money taken or refunded in cash goes to the cash account and everything else to the default "
    "bank account until moved. Accounts are provisional; QAR only."
)


def can_manage(user, business_unit):
    return active_grants(user, MANAGE).filter(business_unit=business_unit).exists()


def can_view(user, business_unit):
    return (
        can_view_ledger(user, business_unit) or can_manage(user, business_unit)
        or active_grants(user, UserActionGrant.Action.APPROVE_BANK_REC).filter(business_unit=business_unit).exists()
    )


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Bank and cash runs only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can work with bank and cash.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not can_view(user, business_unit):
        raise WorkflowPermissionDenied(
            "You need permission to view the general ledger, record bank and cash or approve bank reconciliations "
            "in this business unit."
        )


def _require_manager(user, business_unit):
    _require_user(user, business_unit)
    if not can_manage(user, business_unit):
        raise WorkflowPermissionDenied("You need permission to record bank and cash in this business unit.")


def _text(value, field, limit, required=False):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if len(text) > limit:
        raise InvalidTransaction(f"The {field} can be at most {limit} characters.")
    if required and not text:
        raise InvalidTransaction(f"Give the {field}.")
    return text


def _past_day(value, field):
    day = _day(value, timezone.localdate(), field)
    if day > timezone.localdate():
        raise InvalidTransaction(f"The {field} cannot be in the future.")
    return day


def _account(business_unit, account_id, field="account"):
    if type(account_id) is not int:
        raise InvalidTransaction(f"Choose the {field}.")
    account = CashAccount.objects.filter(pk=account_id, business_unit=business_unit, is_synthetic=True).first()
    if account is None:
        raise InvalidTransaction(f"Choose a bank account or cash box of {business_unit.name} as the {field}.")
    return account


def _reconciled(account, day):
    return BankReconciliation.objects.filter(
        account=account, month=day.replace(day=1), status=BankReconciliation.Status.APPROVED,
    ).exists()


def account_row(account, balance=None):
    return {
        "id": account.pk, "name": account.name, "kind": account.kind, "kind_label": account.get_kind_display(),
        "account_type": account.account_type, "account_type_label": account.get_account_type_display(),
        "is_facility": account.account_type in FACILITIES,
        "bank_name": account.bank_name, "account_last4": account.account_last4, "currency": account.currency,
        "opening_balance": format(account.opening_balance, "f"), "opening_date": account.opening_date.isoformat(),
        "is_default": account.is_default,
        **({} if balance is None else {"balance": format(balance, "f")}),
    }


@transaction.atomic
def create_account(*, actor, business_unit, name, kind, bank_name="", account_last4="", opening_balance="0.00",
                   opening_date=None, is_default=False, account_type="current"):
    _require_manager(actor, business_unit)
    from .access_levels import require_master_admin
    require_master_admin(actor, "open a bank account or cash box")
    name = _text(name, "account name", 80, required=True)
    if kind not in CashAccount.Kind.values:
        raise InvalidTransaction("Choose whether this is a bank account or a cash box.")
    if kind == CashAccount.Kind.CASH and CashAccount.objects.filter(business_unit=business_unit, kind=kind).exists():
        raise InvalidTransaction(f"{business_unit.name} already has its cash account. A unit has one cash account.")
    if kind == CashAccount.Kind.BANK and account_type not in CashAccount.AccountType.values:
        raise InvalidTransaction("Choose the type of bank account: current, savings, LTR, LG or other.")
    account_type = account_type if kind == CashAccount.Kind.BANK else ""
    bank_name = _text(bank_name, "bank name", 80) if kind == CashAccount.Kind.BANK else ""
    last4 = _text(account_last4, "last four digits", 4) if kind == CashAccount.Kind.BANK else ""
    if last4 and not (len(last4) == 4 and last4.isdigit()):
        raise InvalidTransaction("Give the last four digits of the account number, or leave them empty.")
    opening = _money(opening_balance, "opening balance")
    if account_type == CashAccount.AccountType.LTR:
        opening = -opening  # an LTR account opens with the amount owed to the bank
    day = _past_day(opening_date, "opening date")
    if CashAccount.objects.filter(business_unit=business_unit).annotate(lowered=Lower("name")).filter(
        lowered=name.lower(),
    ).exists():
        raise InvalidTransaction(f"{business_unit.name} already has an account called {name}.")
    same_kind = CashAccount.objects.select_for_update().filter(business_unit=business_unit, kind=kind)
    first = not same_kind.exists()
    if is_default and not first:
        same_kind.filter(is_default=True).update(is_default=False)
    account = CashAccount.objects.create(
        business_unit=business_unit, name=name, kind=kind, account_type=account_type, bank_name=bank_name, account_last4=last4,
        opening_balance=opening, opening_date=day, is_default=bool(is_default) or first, created_by=actor,
    )
    return account_row(account, opening)


def _cash_lines(business_unit):
    return (
        JournalLine.objects.filter(
            entry__business_unit=business_unit, entry__status=JournalEntry.Status.POSTED,
            account__code=CASH_CODE, is_synthetic=True,
        )
        .select_related("entry", "cash_assignment", "cash_match")
        .order_by("entry__document_date", "entry__pk", "pk")
    )


def _router(business_unit):
    """Return a function giving the account id each cash line belongs to (or None when no account fits)."""
    defaults = dict(
        CashAccount.objects.filter(business_unit=business_unit, is_default=True).values_list("kind", "pk")
    )
    cash_operations = set(QuotePayment.objects.filter(
        quote__business_unit=business_unit, method=QuotePayment.Method.SIMULATED_CASH,
    ).values_list("idempotency_id", flat=True)) | set(CustomerCreditNote.objects.filter(
        business_unit=business_unit, refund_method=CustomerCreditNote.RefundMethod.CASH,
    ).values_list("idempotency_id", flat=True))

    def route(line):
        assignment = getattr(line, "cash_assignment", None)
        if assignment is not None:
            return assignment.account_id
        kind = CashAccount.Kind.CASH if line.entry.idempotency_id in cash_operations else CashAccount.Kind.BANK
        return defaults.get(kind)

    return route


def _line_row(line, account_id, balance):
    amount_in, amount_out = line.debit, line.credit
    return {
        "line_id": line.pk, "date": line.entry.document_date.isoformat(), "journal_entry_id": line.entry_id,
        "source": line.entry.get_kind_display().replace("Synthetic ", "").capitalize(), "source_kind": line.entry.kind,
        "description": re.sub(r"^TEST ONLY[^:]*:\s*", "", line.entry.description), "memo": line.memo,
        "money_in": format(amount_in, "f") if amount_in else "", "money_out": format(amount_out, "f") if amount_out else "",
        "balance": format(balance, "f"), "account_id": account_id,
        "fixed": line.entry.kind == JournalEntry.Kind.CASH_ENTRY,
    }


def cash_overview(*, actor, business_unit, account_id=None, start=None, end=None):
    _require_user(actor, business_unit)
    end = _day(end, timezone.localdate(), "end")
    start = _day(start, end.replace(day=1), "start")
    if start > end:
        raise InvalidTransaction("The start date must be on or before the end date.")
    accounts = sorted(CashAccount.objects.filter(business_unit=business_unit, is_synthetic=True),
                      key=lambda account: (account.kind != CashAccount.Kind.CASH, account.bank_name.lower(),
                                           TYPE_ORDER.get(account.account_type, 0), account.name.lower()))
    by_id = {account.pk: account for account in accounts}
    route = _router(business_unit)
    balance = {account.pk: account.opening_balance for account in accounts}
    opening = dict(balance)
    period = {account.pk: [ZERO, ZERO] for account in accounts}
    unplaced, unplaced_total = [], ZERO
    preferred = sorted(accounts, key=lambda account: (not account.is_default, account.kind != CashAccount.Kind.BANK))
    selected = by_id.get(account_id) if account_id is not None else (preferred[0] if preferred else None)
    if account_id is not None and selected is None:
        raise InvalidTransaction("Choose a bank account or cash box of this business unit.")
    movements = []
    for line in _cash_lines(business_unit):
        day = line.entry.document_date
        if day > end:
            break
        target = route(line)
        account = by_id.get(target)
        if account is None or day < account.opening_date:
            if account is None and day >= start:
                unplaced_total += line.debit - line.credit
                unplaced.append(_line_row(line, None, unplaced_total))
            continue
        balance[target] += line.debit - line.credit
        if day < start:
            opening[target] = balance[target]
            continue
        period[target][0] += line.debit
        period[target][1] += line.credit
        if selected is not None and target == selected.pk:
            movements.append(_line_row(line, target, balance[target]))
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "start": start.isoformat(), "end": end.isoformat(),
        "can_manage": can_manage(actor, business_unit),
        "accounts": [account_row(account, balance[account.pk]) | {
            "opening": format(opening[account.pk], "f"),
            "money_in": format(period[account.pk][0], "f"), "money_out": format(period[account.pk][1], "f"),
        } for account in accounts],
        "total": format(sum((value for key, value in balance.items() if by_id[key].account_type not in FACILITIES), ZERO), "f"),
        "facilities_total": format(sum((value for key, value in balance.items() if by_id[key].account_type in FACILITIES), ZERO), "f"),
        "account_types": [{"value": value, "label": label} for value, label in CashAccount.AccountType.choices],
        "selected_id": selected.pk if selected else None,
        "movements": movements,
        "unplaced": unplaced,
        "entry_kinds": [{"value": value, "label": label} for value, label in CashEntry.Kind.choices],
        "currency": "QAR",
        "policy_label": POLICY_LABEL,
    }


@transaction.atomic
def move_line(*, actor, business_unit, line_id, account_id):
    """Say which account a cash line belongs to, for example a card payment that went to a second bank."""
    _require_manager(actor, business_unit)
    if type(line_id) is not int:
        raise InvalidTransaction("Choose the movement to move.")
    line = _cash_lines(business_unit).filter(pk=line_id).first()
    if line is None:
        raise InvalidTransaction("Movement not found in this business unit.")
    if line.entry.kind == JournalEntry.Kind.CASH_ENTRY:
        raise InvalidTransaction("Entries recorded on this screen stay on the account they were recorded on.")
    account = _account(business_unit, account_id)
    if hasattr(line, "cash_match"):
        raise InvalidTransaction("This movement is matched to a statement line. Unmatch it before moving it.")
    current = _router(business_unit)(line)
    for each in CashAccount.objects.filter(pk__in=[pk for pk in (current, account.pk) if pk]):
        if _reconciled(each, line.entry.document_date):
            raise InvalidTransaction(f"{line.entry.document_date:%B %Y} is reconciled and approved for {each.name}.")
    if line.entry.document_date < account.opening_date:
        raise InvalidTransaction(f"{account.name} opened on {account.opening_date.isoformat()}, after this movement.")
    CashLineAssignment.objects.update_or_create(
        journal_line=line, defaults={"account": account, "assigned_by": actor, "assigned_at": timezone.now()},
    )
    return {"line_id": line.pk, "account_id": account.pk}


@transaction.atomic
def record_entry(*, actor, idempotency_key, business_unit, kind, account_id, amount, entry_date=None,
                 description="", reference="", to_account_id=None):
    """Post a transfer between two accounts, bank charges or interest received."""
    _require_manager(actor, business_unit)
    if kind not in CashEntry.Kind.values:
        raise InvalidTransaction("Choose a transfer, bank charges or interest.")
    account = _account(business_unit, account_id, "account" if kind != CashEntry.Kind.TRANSFER else "account it leaves")
    other = None
    if kind == CashEntry.Kind.TRANSFER:
        other = _account(business_unit, to_account_id, "account it goes to")
        if other.pk == account.pk:
            raise InvalidTransaction("A transfer needs two different accounts.")
    amount = _money(amount, "amount")
    if amount <= 0:
        raise InvalidTransaction("The amount must be greater than zero.")
    day = _past_day(entry_date, "date")
    for each in filter(None, (account, other)):
        if day < each.opening_date:
            raise InvalidTransaction(f"{each.name} opened on {each.opening_date.isoformat()}, after this date.")
        if _reconciled(each, day):
            raise InvalidTransaction(f"{day:%B %Y} is reconciled and approved for {each.name}. Use a date in a later month.")
    default_text = {
        CashEntry.Kind.TRANSFER: f"Transfer from {account.name} to {other.name}" if other else "",
        CashEntry.Kind.BANK_CHARGE: f"Bank charges on {account.name}",
        CashEntry.Kind.INTEREST: f"Interest on {account.name}",
    }[kind]
    description = _text(description, "description", 160) or default_text
    reference = _text(reference, "reference", 80)
    payload = {"unit": business_unit.pk, "kind": kind, "account_id": account.pk, "to_account_id": other.pk if other else None,
               "amount": amount, "date": day.isoformat(), "description": description, "reference": reference}
    operation, created = _begin_idempotent_operation(idempotency_key, "cash_entry", payload)
    if not created:
        return operation.result
    if kind == CashEntry.Kind.TRANSFER:
        lines = [(CASH_CODE, amount, ZERO, f"Transfer in from {account.name}"),
                 (CASH_CODE, ZERO, amount, f"Transfer out to {other.name}")]
        owners = [other, account]
    elif kind == CashEntry.Kind.BANK_CHARGE:
        lines = [("TEST_BANK_CHARGES", amount, ZERO, description), (CASH_CODE, ZERO, amount, description)]
        owners = [None, account]
    else:
        lines = [(CASH_CODE, amount, ZERO, description), ("TEST_INTEREST_INCOME", ZERO, amount, description)]
        owners = [account, None]
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.CASH_ENTRY, description=f"TEST ONLY: {description}", lines=lines, document_date=day,
    )
    for line, owner in zip(entry.lines.order_by("pk"), owners):
        if owner is not None:
            CashLineAssignment.objects.create(journal_line=line, account=owner, assigned_by=actor)
    cash_entry = CashEntry.objects.create(
        idempotency=operation, business_unit=business_unit, kind=kind, account=account, to_account=other, amount=amount,
        entry_date=day, description=description, reference=reference, journal_entry=entry, recorded_by=actor,
    )
    return _complete(operation, {
        "id": cash_entry.pk, "kind": kind, "kind_label": cash_entry.get_kind_display(), "account_id": account.pk,
        "to_account_id": other.pk if other else None, "amount": format(amount, "f"), "date": day.isoformat(),
        "description": description, "reference": reference, "journal_entry_id": entry.pk,
    })

