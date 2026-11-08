"""Unpaid invoices and supplier bills brought over from the old system, and the money paid on them later.

Part of the go-live imports (go_live_imports.py). Provisional rules until Tivoli confirms them:

- Customer balances: one row per unpaid old invoice, with the customer's client number (customers keep
  their old numbers), the invoice number, its date and what is still owed, in QAR.
- Supplier balances: one row per unpaid old supplier bill, with the supplier code, the bill number, its
  date, the currency and the amount still owed. For a bill in another currency, give either its QAR value
  or the monthly rate the old system used for it, and the ERP works out the QAR value from the rate.
- Approving the file books, per business unit and on the balances day, receivables (customers) or
  receipt clearing (suppliers) against the "opening balances" account, which the trial balance left
  waiting for them. Each old invoice or bill then shows in Who owes us, in Who we owe and on the
  customer statement, aged from its own date.
- Money received or paid on an old balance is prepared by one person and approved by another, and
  names the bank account or cash box. Customer receipts: prepared with 'Record bank and cash' and
  approved with 'Approve and post manual journal entries'. Supplier payments: the usual supplier
  payment permissions. A foreign-currency bill paid at a different rate books the difference as an
  exchange gain or loss.
- Nothing more than what is still owed can be paid, counting payments waiting for approval.
"""

import datetime
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .approval_limits import require_within_limit
from . import cash_accounts, manual_journals, supplier_payments
from .go_live_balances import OPENING, _amount, _unit
from .go_live_items import find_supplier
from .models import (
    BASE_CURRENCY, BusinessUnit, CashAccount, CashLineAssignment, Currency, JournalEntry, OldBalance, OldBalanceSettlement,
    SyntheticCounterparty, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied, _exchange_terms, _to_base
from .supplier_payments import _require_user
from .transaction_services import InvalidTransaction, _begin_idempotent_operation, _complete, _money, _post_journal

A = UserActionGrant.Action
ZERO = Decimal("0.00")
SIDE = OldBalance.Side
ST = OldBalanceSettlement.Status
CUSTOMER_COLUMNS = [
    ("Business unit", "business_unit", True, "The business unit code, for example TVL.", "TVL"),
    ("Client number", "party", True, "The customer's client number, as in the customers import.", "C-004512"),
    ("Invoice number", "reference", True, "The old system's invoice number.", "INV-2026-0815"),
    ("Invoice date", "document_date", True, "The invoice date. The balance is aged from it.", "2026-08-15"),
    ("Still owed QAR", "amount", True, "What the customer still owes on this invoice, in QAR.", "18500"),
    ("Note", "note", False, "Optional.", ""),
]
SUPPLIER_COLUMNS = [
    ("Business unit", "business_unit", True, "The business unit code, for example TVL.", "TVL"),
    ("Supplier code", "party", True, "The supplier code, as in the suppliers import.", "SUP-0042"),
    ("Bill number", "reference", True, "The supplier's bill or invoice number.", "GT-55120"),
    ("Bill date", "document_date", True, "The bill date. The balance is aged from it.", "2026-09-02"),
    ("Currency", "currency", False, "The bill's currency. Leave blank for QAR.", "EUR"),
    ("Still owed", "amount", True, "What we still owe on this bill, in its currency.", "12000"),
    ("Still owed QAR", "value", False, "Its QAR value. Give this or the rate when the bill is not in QAR.", ""),
    ("Rate", "rate", False, "The monthly rate the old system used for this bill: QAR for one unit of its currency. "
     "The ERP works out the QAR value from it.", "4.05"),
    ("Note", "note", False, "Optional.", ""),
]
POLICY_LABEL = ("TEST ONLY provisional rules: customer receipts on old balances are prepared with 'Record bank and cash' and "
                "approved by someone else with 'Approve and post manual journal entries'; supplier payments on old balances "
                "use the supplier payment permissions; nothing more than what is still owed can be paid; a foreign-currency "
                "bill paid at another rate books the difference as an exchange gain or loss; a foreign-currency bill comes in at "
                "the QAR value given, or at the monthly rate the old system used for it.")


# Import: checking rows and creating the balances.
def _date(value, label, day, problems):
    text = str(value or "").strip()
    if not text:
        problems.append(f"{label} is missing.")
        return ""
    for form in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            found = datetime.datetime.strptime(text[:10], form).date()
            break
        except ValueError:
            found = None
    if found is None:
        problems.append(f"{label} must be a date, for example 2026-08-15 or 15/08/2026.")
        return ""
    if day and found > day:
        problems.append(f"{label} is after the balances day ({day:%d %b %Y}).")
    return found.isoformat()


def _foreign_value(currency, amount, values, problems):
    """The QAR value of a foreign-currency bill: as given, or the amount at the old system's monthly rate."""
    given, rate_text = str(values.get("value", "") or "").strip(), str(values.get("rate", "") or "").strip()
    if not given and not rate_text:
        problems.append(f"Give the QAR value of this {currency} bill, or the monthly rate the old system used for it.")
        return ""
    value = _amount(given, "Still owed QAR", problems, positive=True) if given else ""
    if not rate_text:
        return value
    try:
        rate = Decimal(rate_text.replace(",", ""))
    except InvalidOperation:
        rate = None
    if rate is None or not rate.is_finite() or rate <= 0 or rate > 10000 or rate.as_tuple().exponent < -8:
        problems.append(f"Rate must be the QAR for one {currency}, for example 4.05.")
        return value
    if not amount:
        return value
    worked_out = (Decimal(amount) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value and abs(Decimal(value) - worked_out) > 1:
        problems.append(f"Still owed QAR ({Decimal(value):,.2f}) does not match the amount at the rate ({worked_out:,.2f}). "
                        "Give one of them, or correct it.")
        return value
    return value or format(worked_out, "f")


def check_row(side, values, seen, problems):
    row = {"business_unit": _unit(values, problems)}
    given = " ".join(values.get("party", "").split())
    if side == SIDE.CUSTOMER:
        party = SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.CUSTOMER, client_number__iexact=given).first() if given else None
        if not given:
            problems.append("Client number is missing.")
        elif party is None:
            problems.append(f"No customer has the client number {given}. Import the customers first.")
        row["party"] = party.client_number if party else given
    else:
        party = find_supplier(given) if given else None
        if not given:
            problems.append("Supplier code is missing.")
        elif party is None:
            problems.append(f"No supplier has the code {given}. Import the suppliers first.")
        row["party"] = party.code if party else given
    row["reference"] = " ".join(values.get("reference", "").split())[:60]
    if not row["reference"]:
        problems.append(("Invoice" if side == SIDE.CUSTOMER else "Bill") + " number is missing.")
    row["document_date"] = _date(values.get("document_date"), "Invoice date" if side == SIDE.CUSTOMER else "Bill date",
                                 seen.get("day"), problems)
    currency = (values.get("currency", "") or BASE_CURRENCY).strip().upper() if side == SIDE.SUPPLIER else BASE_CURRENCY
    if currency != BASE_CURRENCY and not Currency.objects.filter(code=currency, is_active=True).exists():
        problems.append(f"{currency} is not a currency the ERP uses.")
    row["currency"] = currency
    label = "Still owed QAR" if side == SIDE.CUSTOMER else "Still owed"
    row["amount"] = _amount(values.get("amount", ""), label, problems, positive=True)
    if not values.get("amount"):
        problems.append(f"{label} is missing.")
    if side == SIDE.CUSTOMER or currency == BASE_CURRENCY:
        row["value"] = row["amount"]
    else:
        row["value"] = _foreign_value(currency, row["amount"], values, problems)
    row["note"] = " ".join(values.get("note", "").split())[:120]
    key = (row["business_unit"], row["party"].upper(), row["reference"].upper())
    if party is not None and row["reference"] and not problems:
        if key in seen["old"]:
            problems.append(f"{row['reference']} for {party.name} is also on row {seen['old'][key]}.")
        elif OldBalance.objects.filter(business_unit__code=row["business_unit"], party=party, reference__iexact=row["reference"]).exists():
            problems.append(f"{row['reference']} for {party.name} is already in the ERP.")
    return row


def remember(row, number, seen):
    seen["old"].setdefault((row["business_unit"], row["party"].upper(), row["reference"].upper()), number)


def _party(side, code):
    if side == SIDE.CUSTOMER:
        return SyntheticCounterparty.objects.get(party_type=SyntheticCounterparty.Type.CUSTOMER, client_number=code)
    return SyntheticCounterparty.objects.get(party_type=SyntheticCounterparty.Type.SUPPLIER, code=code)


def create(side, batch, actor):
    """The balances, and one journal per business unit against opening balances."""
    units = {unit.code: unit for unit in BusinessUnit.objects.filter(code__in={row["business_unit"] for row in batch.rows})}
    by_unit, ids = defaultdict(list), []
    for row in batch.rows:
        by_unit[row["business_unit"]].append(row)
    for code, rows in by_unit.items():
        unit = units[code]
        operation, _ = _begin_idempotent_operation(f"go-live-{side}-balances-{batch.pk}-{code}", f"go_live_{side}_balances",
                                                   {"batch": batch.pk, "unit": code})
        total = ZERO
        for row in rows:
            balance = OldBalance.objects.create(
                batch=batch, business_unit=unit, side=side, party=_party(side, row["party"]), reference=row["reference"],
                document_date=datetime.date.fromisoformat(row["document_date"]), currency=row["currency"],
                amount=Decimal(row["amount"]), value=Decimal(row["value"]), note=row["note"])
            total += balance.value
            ids.append(balance.pk)
        what = "Customer balances" if side == SIDE.CUSTOMER else "Supplier balances"
        lines = ([("TEST_RECEIVABLE", total, ZERO, f"{what} from the old system"), (OPENING, ZERO, total, f"{what} brought in")]
                 if side == SIDE.CUSTOMER else
                 [(OPENING, total, ZERO, f"{what} brought in"), ("TEST_GRNI", ZERO, total, f"{what} from the old system")])
        entry = _post_journal(legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation,
                              kind=JournalEntry.Kind.OPENING_BALANCE, document_date=batch.balances_on,
                              description=f"TEST ONLY {what.lower()} at go-live (import {batch.pk})", lines=lines)
        _complete(operation, {"batch": batch.pk, "unit": code, "journal": entry.pk})
    return ids


# What is still owed.
def settled(balance, as_of=None):
    """QAR cleared and the amount paid in the balance's currency by approved settlements, up to a day."""
    approved = balance.settlements.filter(status=ST.APPROVED)
    if as_of is not None:
        approved = approved.filter(paid_on__lte=as_of)
    totals = approved.aggregate(cleared=Sum("cleared_value"), paid=Sum("amount"))
    return totals["cleared"] or ZERO, totals["paid"] or ZERO


def _waiting(balance):
    return balance.settlements.filter(status=ST.REQUESTED).aggregate(total=Sum("amount"))["total"] or ZERO


def open_balances(business_unit, side, as_of, party=None):
    """[(balance, owed QAR, owed in its currency, cleared QAR)] for balances dated on or before as_of with something left."""
    balances = OldBalance.objects.filter(business_unit=business_unit, side=side, document_date__lte=as_of).select_related("party")
    if party is not None:
        balances = balances.filter(party=party)
    rows = []
    for balance in balances:
        cleared, paid = settled(balance, as_of)
        if balance.value - cleared > 0:
            rows.append((balance, balance.value - cleared, balance.amount - paid, cleared))
    return rows


# Receipts and payments on old balances.
def can_prepare(user, business_unit, side):
    if side == SIDE.CUSTOMER:
        return cash_accounts.can_manage(user, business_unit)
    return supplier_payments.can_pay(user, business_unit)


def can_approve(user, business_unit, side):
    if side == SIDE.CUSTOMER:
        return manual_journals.can_approve(user, business_unit)
    return supplier_payments.can_approve(user, business_unit)


def _side(value):
    if value not in SIDE.values:
        raise InvalidTransaction("Choose customer or supplier balances.")
    return value


def _require_any(actor, business_unit, side):
    _require_user(actor, business_unit)
    if not (can_prepare(actor, business_unit, side) or can_approve(actor, business_unit, side)):
        what = ("record bank and cash or approve manual journals" if side == SIDE.CUSTOMER
                else "prepare or approve supplier payments")
        raise WorkflowPermissionDenied(f"You need permission to {what} in this business unit.")


def _fmt(value):
    return format((value or ZERO).quantize(Decimal("0.01")), "f")


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _settlement_row(item, actor):
    balance = item.balance
    return {
        "id": item.pk, "balance_id": balance.pk, "reference": balance.reference, "party": balance.party.name,
        "status": item.status, "status_label": item.get_status_display(), "currency": balance.currency,
        "amount": _fmt(item.amount), "exchange_rate": format(item.exchange_rate.normalize(), "f"), "paid_value": _fmt(item.paid_value),
        "cleared_value": _fmt(item.cleared_value) if item.cleared_value is not None else None,
        "paid_on": item.paid_on.isoformat(), "cash_account": item.cash_account.name, "bank_reference": item.bank_reference,
        "prepared_by": _name(item.prepared_by), "decided_by": _name(item.decided_by), "decision_note": item.decision_note,
        "journal_entry_id": item.journal_entry_id,
        "can_decide": item.status == ST.REQUESTED and item.prepared_by_id != actor.pk
        and can_approve(actor, balance.business_unit, balance.side),
    }


def overview(*, actor, business_unit, side):
    side = _side(side)
    _require_any(actor, business_unit, side)
    today = timezone.localdate()
    rows = []
    for balance, owed, owed_in_currency, _cleared in open_balances(business_unit, side, today):
        waiting = _waiting(balance)
        rows.append({
            "id": balance.pk, "party": {"id": balance.party_id, "name": balance.party.name,
                                        "number": balance.party.client_number if side == SIDE.CUSTOMER else balance.party.code},
            "reference": balance.reference, "date": balance.document_date.isoformat(),
            "age_days": (today - balance.document_date).days, "currency": balance.currency,
            "amount": _fmt(balance.amount), "value": _fmt(balance.value), "owed": _fmt(owed),
            "owed_in_currency": _fmt(owed_in_currency), "waiting": _fmt(waiting),
            "can_pay_more": owed_in_currency - waiting > 0,
        })
    settlements = (OldBalanceSettlement.objects.filter(balance__business_unit=business_unit, balance__side=side)
                   .select_related("balance__party", "balance__business_unit", "cash_account", "prepared_by", "decided_by"))
    accounts = CashAccount.objects.filter(business_unit=business_unit).order_by("kind", "name")
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "side": side, "balances": rows,
        "total_owed": _fmt(sum((Decimal(row["owed"]) for row in rows), ZERO)),
        "waiting": [_settlement_row(item, actor) for item in settlements if item.status == ST.REQUESTED],
        "recent": [_settlement_row(item, actor) for item in settlements if item.status != ST.REQUESTED][:30],
        "accounts": [{"id": account.pk, "name": account.name, "kind": account.kind} for account in accounts],
        "can_prepare": can_prepare(actor, business_unit, side), "can_approve": can_approve(actor, business_unit, side),
        "policy": POLICY_LABEL,
    }


def _paid_on(value):
    try:
        day = datetime.date.fromisoformat(str(value or ""))
    except ValueError:
        raise InvalidTransaction("Give the payment date as YYYY-MM-DD.") from None
    if day > timezone.localdate():
        raise InvalidTransaction("The payment date cannot be in the future.")
    return day


@transaction.atomic
def prepare(*, actor, business_unit, data):
    """Prepare a receipt from a customer or a payment to a supplier on an old balance. Nothing is booked yet."""
    _require_user(actor, business_unit)
    balance = (OldBalance.objects.select_for_update().select_related("party")
               .filter(pk=data.get("balance_id") if type(data.get("balance_id")) is int else None, business_unit=business_unit).first())
    if balance is None:
        raise InvalidTransaction(f"Choose an old balance of {business_unit.name}.")
    if not can_prepare(actor, business_unit, balance.side):
        what = "'Record bank and cash'" if balance.side == SIDE.CUSTOMER else "'Prepare supplier payments'"
        raise WorkflowPermissionDenied(f"You need {what} to record money on this old balance.")
    amount = _money(data.get("amount"), "amount")
    if amount <= 0:
        raise InvalidTransaction("Give an amount above zero.")
    _cleared, paid = settled(balance)
    left = balance.amount - paid - _waiting(balance)
    if amount > left:
        raise InvalidTransaction(f"Only {balance.currency} {left:,.2f} is left to pay on {balance.reference}, "
                                 "counting payments waiting for approval.")
    paid_on = _paid_on(data.get("paid_on"))
    if paid_on < balance.batch.balances_on:
        raise InvalidTransaction("The payment date must be after the go-live balances day.")
    terms = _exchange_terms(balance.currency, data.get("exchange_rate"), data.get("rate_source", ""), data.get("rate_date"))
    account = CashAccount.objects.filter(pk=data.get("cash_account_id") if type(data.get("cash_account_id")) is int else None,
                                         business_unit=business_unit).first()
    if account is None:
        raise InvalidTransaction("Choose the bank account or cash box the money " + ("came into." if balance.side == SIDE.CUSTOMER else "left from."))
    if account.opening_date > paid_on:
        raise InvalidTransaction(f"{account.name} opens on {account.opening_date:%d %b %Y}, after the payment date.")
    reference = " ".join(str(data.get("bank_reference") or "").split())[:80]
    item = OldBalanceSettlement.objects.create(
        balance=balance, amount=amount, exchange_rate=terms["exchange_rate"], rate_source=terms["rate_source"],
        rate_date=terms["rate_date"], paid_value=_to_base(amount, terms["exchange_rate"]), paid_on=paid_on,
        cash_account=account, bank_reference=reference, prepared_by=actor)
    return _settlement_row(item, actor)


@transaction.atomic
def decide(*, actor, business_unit, settlement_id, action, note=""):
    _require_user(actor, business_unit)
    item = (OldBalanceSettlement.objects.select_for_update()
            .filter(pk=settlement_id if type(settlement_id) is int else None, balance__business_unit=business_unit)
            .select_related("balance__party", "balance__business_unit", "cash_account").first())
    if item is None:
        raise InvalidTransaction("That payment was not found in this business unit.")
    if item.status != ST.REQUESTED:
        raise InvalidTransaction(f"This payment is already {item.get_status_display().lower()}.")
    balance = item.balance
    if not can_approve(actor, business_unit, balance.side):
        what = "'Approve and post manual journal entries'" if balance.side == SIDE.CUSTOMER else "'Approve supplier payments'"
        raise WorkflowPermissionDenied(f"You need {what} to decide this payment.")
    if item.prepared_by_id == actor.pk:
        raise WorkflowPermissionDenied("You prepared this payment, so someone else must decide it.")
    if action == "approve":
        require_within_limit(actor, business_unit, A.APPROVE_JOURNAL if balance.side == SIDE.CUSTOMER else A.APPROVE_PAYMENT,
                             item.paid_value, "this payment")
    item.decided_by, item.decided_at = actor, timezone.now()
    if action == "reject":
        note = " ".join(str(note or "").split())[:200]
        if not note:
            raise InvalidTransaction("Give the reason.")
        item.status, item.decision_note = ST.REJECTED, note
        item.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        return _settlement_row(item, actor)
    if action != "approve":
        raise InvalidTransaction("Approve or reject.")
    OldBalance.objects.select_for_update().get(pk=balance.pk)
    cleared_before, paid_before = settled(balance)
    if paid_before + item.amount >= balance.amount:
        cleared = balance.value - cleared_before  # the last payment clears what is left, so nothing is left over by rounding
    else:
        cleared = (item.amount * balance.value / balance.amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    difference = item.paid_value - cleared
    who = balance.party.name
    memo = f"{balance.reference} (old system)"
    if balance.side == SIDE.CUSTOMER:
        lines = [("TEST_CASH", item.paid_value, ZERO, f"Received from {who} on {memo}"[:200]),
                 ("TEST_RECEIVABLE", ZERO, cleared, f"{who}: {memo}"[:200])]
    else:
        lines = [("TEST_GRNI", cleared, ZERO, f"{who}: {memo}"[:200]),
                 ("TEST_CASH", ZERO, item.paid_value, f"Paid to {who} on {memo}"[:200])]
        if difference:
            lines.append(("TEST_FX_LOSS", difference, ZERO, "Exchange difference") if difference > 0
                         else ("TEST_FX_GAIN", ZERO, -difference, "Exchange difference"))
    operation, _ = _begin_idempotent_operation(f"old-balance-settlement-{item.pk}", "old_balance_settlement", {"settlement": item.pk})
    entry = _post_journal(legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
                          kind=JournalEntry.Kind.OLD_BALANCE_SETTLEMENT, document_date=item.paid_on,
                          description=f"TEST ONLY: {'receipt from' if balance.side == SIDE.CUSTOMER else 'payment to'} {who} "
                                      f"on old balance {balance.reference}"[:240], lines=lines)
    cash_line = entry.lines.get(account__code="TEST_CASH")
    CashLineAssignment.objects.create(journal_line=cash_line, account=item.cash_account, assigned_by=actor)
    _complete(operation, {"settlement": item.pk, "journal_entry_id": entry.pk})
    item.status, item.cleared_value, item.journal_entry = ST.APPROVED, cleared, entry
    item.save(update_fields=["status", "decided_by", "decided_at", "cleared_value", "journal_entry"])
    return _settlement_row(item, actor)

