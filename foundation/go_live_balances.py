"""Go-live import of opening stock and the opening trial balance, booked on the day before go-live.

Part of the go-live imports (go_live_imports.py): the same template, check, approve flow. The
person preparing the file also gives the day the balances are as at; it must be in an open month.
Provisional rules until Tivoli confirms them:

- Opening stock: one row per business unit, location and finish (SKU), with the quantity and the
  unit cost in QAR. It becomes stock at that cost and is booked as stock (inventory) against the
  "opening balances" account. Virtual locations (in transit, missing) cannot take opening stock,
  and a place that already has stock in the ERP is refused, so nothing is counted twice.
- Opening trial balance: one row per business unit and ERP account, with the debit or the credit.
  Each business unit must balance on its own. The stock, customer, supplier, fixed asset and staff
  loan lines are brought in by their own imports with every detail, so the trial balance amounts on
  those accounts go to the "opening balances" account instead. Once all of them are in, "opening balances" is
  back at zero; the screen shows what is still left on it.
- Bank and cash lines name the bank account or cash box they belong to, so the Bank and cash
  screen and the books agree. That account must have no opening balance of its own, or the
  money would be counted twice.
- Every other account is booked as given.
"""

import datetime
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import Sum
from django.utils import timezone

from .models import BusinessUnit, CashAccount, CashLineAssignment, JournalEntry, JournalLine, Location, StockBalance, StockMovement, Variant
from .transaction_services import _TEST_ACCOUNTS, InvalidTransaction, _begin_idempotent_operation, _complete, _post_journal

ZERO = Decimal("0.00")
OPENING = "TEST_OPENING_BALANCES"
# Their own imports bring these in with the detail behind them.
BROUGHT_IN_ELSEWHERE = {
    "TEST_INVENTORY": "opening stock",
    "TEST_RECEIVABLE": "customer balances",
    "TEST_GRNI": "supplier balances",
    "TEST_FIXED_ASSETS": "fixed assets",
    "TEST_ACCUM_DEPRECIATION": "fixed assets",
    "TEST_STAFF_LOANS": "staff loans",
}
ACCOUNTS = {code: name.replace("TEST ONLY — ", "") for code, (name, _class) in _TEST_ACCOUNTS.items() if code != OPENING}

STOCK_COLUMNS = [
    ("Business unit", "business_unit", True, "The business unit code, for example TVL.", "TVL"),
    ("Location", "location", True, "The location code, for example W41-01. Not a virtual location.", "W41-01"),
    ("Finish code (SKU)", "sku", True, "The finish code of the item, as in Stock items or the items import.", "TVF-0142-SAN"),
    ("Quantity", "quantity", True, "How many are there, more than zero.", "4"),
    ("Unit cost QAR", "unit_cost", True, "What one costs us, in QAR, including shipping and clearance.", "3650"),
]
TB_COLUMNS = [
    ("Business unit", "business_unit", True, "The business unit code, for example TVL.", "TVL"),
    ("Account", "account", True, "The ERP account code from the 'ERP accounts' sheet, or its name.", "TEST_CASH"),
    ("Debit", "debit", False, "Debit balance in QAR. Leave blank when there is a credit.", "125000"),
    ("Credit", "credit", False, "Credit balance in QAR. Leave blank when there is a debit.", ""),
    ("Bank or cash account", "cash_account", False, "Needed on TEST_CASH lines: the name of the bank account or cash box "
     "under Bank and cash.", "QNB current"),
    ("Note", "note", False, "Optional, for example the old system's account.", "QNB 1001"),
]


def _amount(value, label, problems, *, places=2, positive=False):
    if not value:
        return ""
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:
        problems.append(f"{label} must be a number.")
        return ""
    if not number.is_finite() or number < 0 or (positive and number == 0):
        problems.append(f"{label} must be more than zero." if positive else f"{label} cannot be negative.")
        return ""
    if number >= Decimal("100000000"):
        problems.append(f"{label} is too large.")
        return ""
    return format(number.quantize(Decimal(1).scaleb(-places)), "f")


def _unit(values, problems):
    code = values.get("business_unit", "").strip()
    unit = BusinessUnit.objects.filter(code__iexact=code, is_active=True).first() if code else None
    if not code:
        problems.append("Business unit is missing.")
    elif unit is None:
        problems.append(f"There is no active business unit with the code {code}.")
    return unit.code if unit else code.upper()


def check_stock_row(values, seen, problems):
    row = {"business_unit": _unit(values, problems)}
    code = values.get("location", "").strip()
    location = Location.objects.filter(code__iexact=code, is_synthetic=True).first() if code else None
    row["location"] = location.code if location else code
    if not code:
        problems.append("Location is missing.")
    elif location is None:
        problems.append(f"There is no location with the code {code}.")
    elif location.kind == Location.Kind.VIRTUAL:
        problems.append(f"{location.code} is a virtual location and cannot take opening stock.")
    elif BusinessUnit.objects.filter(code=row["business_unit"]).exists() and not location.business_units.filter(
            code=row["business_unit"]).exists():
        problems.append(f"{location.code} is not a location of {row['business_unit']}.")
    sku = values.get("sku", "").upper().replace(" ", "")
    variant = Variant.objects.filter(sku=sku).first() if sku else None
    row["sku"] = sku
    if not sku:
        problems.append("Finish code (SKU) is missing.")
    elif variant is None:
        problems.append(f"No item has the finish code {sku}. Import the items first.")
    row["quantity"] = _amount(values.get("quantity", ""), "Quantity", problems, positive=True)
    if not values.get("quantity"):
        problems.append("Quantity is missing.")
    row["unit_cost"] = _amount(values.get("unit_cost", ""), "Unit cost", problems)
    if not values.get("unit_cost"):
        problems.append("Unit cost is missing.")
    key = (row["business_unit"], row["location"], sku)
    if variant is not None and location is not None and not problems:
        if key in seen["stock"]:
            problems.append(f"{sku} at {location.code} is also on row {seen['stock'][key]}.")
        elif StockBalance.objects.filter(business_unit__code=row["business_unit"], location=location, variant=variant,
                                         on_hand_quantity__gt=0).exists():
            problems.append(f"{sku} at {location.code} already has stock in the ERP.")
    return row


def remember_stock(row, number, seen):
    seen["stock"].setdefault((row["business_unit"], row["location"], row["sku"]), number)


def _account_code(value):
    text = " ".join(value.split())
    upper = text.upper().replace(" ", "_")
    for code in (upper, f"TEST_{upper}"):
        if code in ACCOUNTS:
            return code
    for code, name in ACCOUNTS.items():
        if name.lower() == text.lower():
            return code
    return ""


def check_tb_row(values, seen, problems):
    row = {"business_unit": _unit(values, problems)}
    given = values.get("account", "")
    row["account"] = _account_code(given) if given else ""
    if not given:
        problems.append("Account is missing.")
    elif not row["account"]:
        problems.append(f"“{given}” is not an ERP account. Use a code from the 'ERP accounts' sheet.")
    row["debit"] = _amount(values.get("debit", ""), "Debit", problems)
    row["credit"] = _amount(values.get("credit", ""), "Credit", problems)
    if row["debit"] and row["credit"] and Decimal(row["debit"]) and Decimal(row["credit"]):
        problems.append("Give either a debit or a credit, not both.")
    elif not (row["debit"] and Decimal(row["debit"])) and not (row["credit"] and Decimal(row["credit"])):
        if not problems:
            problems.append("Give a debit or a credit.")
    named = " ".join(values.get("cash_account", "").split())
    row["cash_account"] = named[:80]
    if row["account"] == "TEST_CASH":
        account = CashAccount.objects.filter(business_unit__code=row["business_unit"], name__iexact=named).first() if named else None
        if not named:
            problems.append("Say which bank account or cash box this cash line is, as named under Bank and cash.")
        elif account is None:
            problems.append(f"{row['business_unit']} has no bank account or cash box called “{named}”. Add it under Bank and cash first.")
        elif account.opening_balance:
            problems.append(f"{account.name} already has an opening balance of {account.opening_balance:,.2f}. "
                            "Set it to zero, or leave this line out, so the money is not counted twice.")
        else:
            row["cash_account"] = account.name
    elif named:
        problems.append("A bank or cash account is only needed on TEST_CASH lines.")
    row["note"] = " ".join(values.get("note", "").split())[:120]
    return row


def remember_tb(row, number, seen):
    totals = seen["tb"].setdefault(row["business_unit"], {"first": number, "debit": ZERO, "credit": ZERO})
    totals["debit"] += Decimal(row["debit"] or "0")
    totals["credit"] += Decimal(row["credit"] or "0")


def finish_tb(checked, seen):
    """Each business unit's lines must balance; say so on its first row."""
    for unit, totals in seen["tb"].items():
        if totals["debit"] != totals["credit"]:
            row = next(row for row in checked if row["row"] == totals["first"])
            row["problems"].append(f"{unit} does not balance: debits {totals['debit']:,.2f}, credits {totals['credit']:,.2f}, "
                                   f"a difference of {abs(totals['debit'] - totals['credit']):,.2f}.")


def balances_day(value):
    try:
        day = datetime.date.fromisoformat(str(value or ""))
    except ValueError:
        raise InvalidTransaction("Give the day the balances are as at, for example 2026-12-31.") from None
    if day > timezone.localdate():
        raise InvalidTransaction("The balances must be as at today or an earlier day.")
    return day


def _units(batch):
    return {unit.code: unit for unit in BusinessUnit.objects.filter(code__in={row["business_unit"] for row in batch.rows})}


def create_stock(batch, actor):
    """Stock for each row, and one journal per business unit: stock against opening balances."""
    units, ids = _units(batch), []
    by_unit = defaultdict(list)
    for row in batch.rows:
        by_unit[row["business_unit"]].append(row)
    for code, rows in by_unit.items():
        unit = units[code]
        operation, _ = _begin_idempotent_operation(f"go-live-stock-{batch.pk}-{code}", "go_live_opening_stock", {"batch": batch.pk, "unit": code})
        total = ZERO
        for row in rows:
            variant = Variant.objects.get(sku=row["sku"])
            location = Location.objects.get(code=row["location"])
            quantity = Decimal(row["quantity"])
            value = (quantity * Decimal(row["unit_cost"])).quantize(Decimal("0.01"))
            balance, _ = StockBalance.objects.get_or_create(legal_entity_id=unit.legal_entity_id, business_unit=unit,
                                                            variant=variant, location=location)
            balance = StockBalance.objects.select_for_update().get(pk=balance.pk)
            StockMovement.objects.create(balance=balance, idempotency=operation, kind=StockMovement.Kind.OPENING,
                                         quantity_delta=quantity, value_delta=value,
                                         memo=f"TEST ONLY opening stock at go-live (import {batch.pk})")
            balance.on_hand_quantity += quantity
            balance.on_hand_value += value
            balance.updated_at = timezone.now()
            balance.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])
            total += value
        if total > 0:
            entry = _post_journal(
                legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation, kind=JournalEntry.Kind.OPENING_BALANCE,
                description=f"TEST ONLY opening stock at go-live (import {batch.pk})", document_date=batch.balances_on,
                lines=[("TEST_INVENTORY", total, ZERO, "Opening stock at go-live"),
                       (OPENING, ZERO, total, "Opening stock at go-live")])
            ids.append(entry.pk)
        _complete(operation, {"batch": batch.pk, "unit": code, "value": format(total, "f")})
    return ids


def create_trial_balance(batch, actor):
    """One journal per business unit. Lines on accounts brought in by their own imports go to opening balances."""
    units, ids = _units(batch), []
    by_unit = defaultdict(list)
    for row in batch.rows:
        by_unit[row["business_unit"]].append(row)
    for code, rows in by_unit.items():
        unit = units[code]
        operation, _ = _begin_idempotent_operation(f"go-live-tb-{batch.pk}-{code}", "go_live_trial_balance", {"batch": batch.pk, "unit": code})
        lines, cash = [], []
        for row in rows:
            account = row["account"]
            memo = row["note"] or ACCOUNTS[account]
            if account in BROUGHT_IN_ELSEWHERE:
                memo = f"{ACCOUNTS[account]}: brought in by the {BROUGHT_IN_ELSEWHERE[account]} import"
                account = OPENING
            lines.append((account, Decimal(row["debit"] or "0"), Decimal(row["credit"] or "0"), memo[:240]))
            cash.append(row["cash_account"] if account == "TEST_CASH" else None)
        entry = _post_journal(legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation,
                              kind=JournalEntry.Kind.OPENING_BALANCE, document_date=batch.balances_on,
                              description=f"TEST ONLY opening trial balance at go-live (import {batch.pk})", lines=lines)
        for line, name in zip(entry.lines.order_by("pk"), cash):
            if name:
                account = CashAccount.objects.get(business_unit=unit, name__iexact=name)
                if account.opening_date > batch.balances_on:
                    raise InvalidTransaction(f"{account.name} opens on {account.opening_date.isoformat()}, after the balances' day. "
                                             "Change its opening date under Bank and cash first.")
                CashLineAssignment.objects.create(journal_line=line, account=account, assigned_by=actor)
        ids.append(entry.pk)
        _complete(operation, {"batch": batch.pk, "unit": code, "journal": entry.pk})
    return ids


def still_to_bring_in():
    """What is left on the opening balances account, per business unit (zero once everything is in)."""
    rows = (JournalLine.objects.filter(account__code=OPENING, entry__status=JournalEntry.Status.POSTED)
            .values("entry__business_unit__code").annotate(debit=Sum("debit"), credit=Sum("credit")))
    return [{"business_unit": row["entry__business_unit__code"], "left": format(row["credit"] - row["debit"], "f")}
            for row in rows if row["credit"] != row["debit"]]
