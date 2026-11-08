"""Payment plan: every payment due each week or month, by bank and currency, within each bank's daily limit.

Read-only apart from bank limits; recurring payments are the cash-flow
forecast's planned items. Provisional rules (synthetic demo only, waiting for
Finance's approval):

- Payments come from the same places as the cash-flow forecast: what we still
  owe suppliers for goods and charges (due a set number of days after the
  supplier's bill, or the receipt when there is no bill, 30 by default),
  supplier payments and advances already prepared (due on their payment day),
  staff expense claims still to repay (due now), and planned payments going out
  (rent, salaries, loan instalments and the like), once, weekly or monthly.
- Anything already past its due day is due now and shown as late.
- Each payment goes from the bank account chosen on the planned payment, or
  else the unit's default bank account.
- Banks work Sunday to Thursday, so a payment due on a Friday or Saturday is
  sent on the next Sunday.
- A bank's daily limit covers foreign-currency transfers only, counted in QAR
  at the latest rate. Payments are lined up in order of due day (prepared ones
  first, then the biggest) and each one is sent whole on its due day if the
  limit has room, otherwise on the next working day that does. Only a payment
  bigger than a whole day's limit is split over several days. QAR payments are
  not limited.
- Weeks run Sunday to Saturday; months are calendar months.
"""

import calendar
import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models.functions import Lower
from django.utils import timezone

from .cash_accounts import _require_manager, _require_user, _text, can_manage
from .cash_forecast import _balance, _days, _fmt, _item_row, _occurrences, rates
from .models import (
    BASE_CURRENCY, BankTransferLimit, CashAccount, CashForecastItem, PurchaseReceipt, SupplierAdvance, SupplierPayment,
)
from .supplier_payments import _payable
from .transaction_services import InvalidTransaction, _money

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
WORKING_DAYS = {6, 0, 1, 2, 3}  # Sunday to Thursday (Monday is 0)
SOURCES = {"suppliers": "Owed to a supplier", "prepared": "Prepared, waiting for approval",
           "staff": "Staff claims to repay", "planned": "Planned payment"}
POLICY_LABEL = ("TEST ONLY provisional rules: suppliers are paid a set number of days after their bill; banks work Sunday "
                "to Thursday; a bank's daily limit covers foreign-currency transfers only, counted in QAR at the latest "
                "rate; payments are lined up by due day, biggest first, and move to the next working day with room. "
                "QAR payments are not limited.")
NO_BANK = "No bank account chosen"


def _working(day):
    while day.weekday() not in WORKING_DAYS:
        day += datetime.timedelta(days=1)
    return day


def _week_start(day):
    return day - datetime.timedelta(days=(day.weekday() + 1) % 7)


def _month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _add_months(day, months):
    month = day.month - 1 + months
    return datetime.date(day.year + month // 12, month % 12 + 1, 1)


def _periods(view, count, today):
    if view == "week":
        start = _week_start(today)
        return [(start + datetime.timedelta(weeks=index), start + datetime.timedelta(weeks=index, days=6)) for index in range(count)]
    first = today.replace(day=1)
    return [(_add_months(first, index), _month_end(_add_months(first, index))) for index in range(count)]


def _account_row(account):
    if account is None:
        return {"id": None, "name": NO_BANK, "bank_name": "", "account_last4": ""}
    return {"id": account.pk, "name": account.name, "bank_name": account.bank_name, "account_last4": account.account_last4}


def _payments(business_unit, today, end, supplier_days, known, default_account):
    """Every payment due up to end: dicts with due, currency, amount, value (QAR), account, source, label, late."""
    rows = []

    def add(due, currency, amount, value, source, label, account=default_account, payee=""):
        amount, value = amount.quantize(CENT), value.quantize(CENT)
        if amount <= 0:
            return
        rows.append({"due": due, "currency": currency, "amount": amount, "value": value, "account": account,
                     "source": source, "label": label, "payee": payee, "late": due < today})

    receipts = (PurchaseReceipt.objects.filter(business_unit=business_unit, is_synthetic=True)
                .select_related("supplier").prefetch_related("charges"))
    for receipt in receipts:
        received = timezone.localtime(receipt.created_at).date()
        items = [_payable(receipt)] + [_payable(receipt, charge) for charge in receipt.charges.all() if charge.is_eligible]
        for item in items:
            # Still owed and not yet paid or prepared, in the currency of the bill or charge.
            open_amount = item.payable - item.committed()
            if open_amount <= 0:
                continue
            since = item.bill.bill_date if item.charge is None and item.bill is not None else received
            what = "goods" if item.charge is None else item.charge.get_category_display().lower()
            add(since + datetime.timedelta(days=supplier_days), item.currency, open_amount, open_amount * item.rate,
                "suppliers", f"Receipt {receipt.pk} · {what}", payee=receipt.supplier.name)
    for payment in (SupplierPayment.objects.filter(business_unit=business_unit, status=SupplierPayment.Status.REQUESTED,
                                                   advance__isnull=True)):
        add(payment.paid_on, payment.currency, payment.amount, payment.paid_value, "prepared",
            f"Payment for receipt {payment.receipt_id}", payee=payment.payee_name)
    for advance in SupplierAdvance.objects.filter(business_unit=business_unit, status=SupplierAdvance.Status.REQUESTED).select_related("supplier"):
        add(advance.paid_on, advance.currency, advance.amount, advance.value, "prepared", f"Advance {advance.number}",
            payee=advance.supplier.name)
    claims = -_balance(business_unit, "TEST_EXPENSE_CLAIMS", today)
    if claims > 0:
        add(today, BASE_CURRENCY, claims, claims, "staff", "Staff expense claims", payee="Staff")
    items = (CashForecastItem.objects.filter(business_unit=business_unit, removed_at__isnull=True,
                                             direction=CashForecastItem.Direction.OUT).select_related("cash_account"))
    for item in items:
        for day in _occurrences(item, today, end):
            add(day, item.currency, item.amount, item.amount * known[item.currency][0], "planned",
                item.get_repeat_display(), account=item.cash_account or default_account, payee=item.name)
    return rows


def _schedule(payments, limits, today):
    """Give each payment its transfer days, keeping foreign-currency transfers within each bank's daily limit."""
    used = defaultdict(lambda: ZERO)  # (bank key, day) -> QAR value of foreign transfers
    order = sorted(payments, key=lambda row: (max(row["due"], today), row["source"] != "prepared", -row["value"], row["payee"]))
    for row in order:
        start = _working(max(row["due"], today))
        bank = (row["account"].bank_name.lower() if row["account"] else "")
        limit = limits.get(bank) if row["currency"] != BASE_CURRENCY else None
        row["limit_bank"] = bank if limit else None
        if limit is None:
            row["transfers"] = [(start, row["amount"], row["value"])]
            continue
        parts, left, day = [], row["value"], start
        if left <= limit:
            # Whole payments are not split: it goes on the first working day with room for all of it.
            while limit - used[(bank, day)] < left:
                day = _working(day + datetime.timedelta(days=1))
            used[(bank, day)] += left
            parts.append((day, left))
            left = ZERO
        while left > 0:
            room = limit - used[(bank, day)]
            if room > 0:
                take = min(room, left)
                used[(bank, day)] += take
                parts.append((day, take))
                left -= take
            if left > 0:
                day = _working(day + datetime.timedelta(days=1))
        # Share the currency amount over the parts in proportion, the last part taking what is left.
        transfers, given = [], ZERO
        for index, (day, value) in enumerate(parts):
            amount = (row["amount"] - given if index == len(parts) - 1
                      else (row["amount"] * value / row["value"]).quantize(CENT))
            given += amount
            transfers.append((day, amount, value))
        row["transfers"] = transfers
    return used


def _line(row, start, stop):
    """One payment as sent in a period; after_due says its last part goes after the working day it is due."""
    parts = [part for part in row["transfers"] if start <= part[0] <= stop]
    first, last = row["transfers"][0][0], row["transfers"][-1][0]
    due_day = _working(row["due"])
    return {
        "due": row["due"].isoformat(), "source": row["source"], "source_label": SOURCES[row["source"]], "label": row["label"],
        "payee": row["payee"], "currency": row["currency"], "late": row["late"],
        "amount": _fmt(sum((part[1] for part in parts), ZERO)), "value": _fmt(sum((part[2] for part in parts), ZERO)),
        "total_amount": _fmt(row["amount"]), "total_value": _fmt(row["value"]),
        "send_on": [{"date": part[0].isoformat(), "amount": _fmt(part[1]), "value": _fmt(part[2])} for part in parts],
        "split": len(row["transfers"]) > 1, "first_day": first.isoformat(), "last_day": last.isoformat(),
        "after_due": last > due_day and not row["late"], "days_after_due": max(0, (last - due_day).days),
    }


def _group(rows, start, stop):
    """Payments sent in one period, by bank account and then currency."""
    accounts = {}
    for row in rows:
        if not any(start <= part[0] <= stop for part in row["transfers"]):
            continue
        key = row["account"].pk if row["account"] else 0
        group = accounts.setdefault(key, {"account": _account_row(row["account"]), "currencies": {}, "lines": [], "value": ZERO})
        line = _line(row, start, stop)
        group["lines"].append(line)
        group["value"] += Decimal(line["value"])
        money = group["currencies"].setdefault(row["currency"], [ZERO, ZERO])
        money[0] += Decimal(line["amount"])
        money[1] += Decimal(line["value"])
    out = []
    for key in sorted(accounts, key=lambda key: (key == 0, accounts[key]["account"]["bank_name"], accounts[key]["account"]["name"])):
        group = accounts[key]
        group["lines"].sort(key=lambda line: (line["first_day"], line["currency"], -Decimal(line["value"])))
        out.append({"account": group["account"], "value": _fmt(group["value"]),
                    "currencies": [{"currency": code, "amount": _fmt(money[0]), "value": _fmt(money[1])}
                                   for code, money in sorted(group["currencies"].items(), key=lambda pair: (pair[0] != BASE_CURRENCY, pair[0]))],
                    "lines": group["lines"]})
    return out


def payment_plan(*, actor, business_unit, view="week", periods=None, supplier_days=None):
    _require_user(actor, business_unit)
    view = view or "week"
    if view not in ("week", "month"):
        raise InvalidTransaction("Show the plan by week or by month.")
    count = _days(periods, "number of periods", 8 if view == "week" else 3)
    most = 26 if view == "week" else 12
    if not 1 <= count <= most:
        raise InvalidTransaction(f"Show from 1 to {most} {view}s.")
    supplier_days = _days(supplier_days, "days we take to pay suppliers", 30)
    today = timezone.localdate()
    spans = _periods(view, count, today)
    end = spans[-1][1]
    known = rates()
    default_account = CashAccount.objects.filter(business_unit=business_unit, kind=CashAccount.Kind.BANK, is_default=True).first()
    limit_rows = list(BankTransferLimit.objects.filter(business_unit=business_unit).select_related("set_by"))
    limits = {row.bank_name.lower(): row.daily_limit for row in limit_rows}
    payments = _payments(business_unit, today, end, supplier_days, known, default_account)
    used = _schedule(payments, limits, today)

    # Foreign-currency transfers per bank and working day, against the limit, and days where more falls due than it allows.
    due_by_day = defaultdict(lambda: ZERO)
    for row in payments:
        if row["limit_bank"]:
            due_by_day[(row["limit_bank"], _working(max(row["due"], today)))] += row["value"]
    names = {row.bank_name.lower(): row.bank_name for row in limit_rows}
    busy_days = [{"bank_name": names[bank], "date": day.isoformat(), "due": _fmt(value), "limit": _fmt(limits[bank]),
                  "over_by": _fmt(value - limits[bank])}
                 for (bank, day), value in sorted(due_by_day.items(), key=lambda pair: (pair[0][1], pair[0][0]))
                 if value > limits[bank] and day <= end]

    rows = []
    for index, (start, stop) in enumerate(spans):
        groups = _group(payments, start, stop)
        total = sum((Decimal(group["value"]) for group in groups), ZERO)
        currencies = defaultdict(lambda: [ZERO, ZERO])
        for group in groups:
            for money in group["currencies"]:
                currencies[money["currency"]][0] += Decimal(money["amount"])
                currencies[money["currency"]][1] += Decimal(money["value"])
        days = []
        if view == "week":
            for offset in range(7):
                day = start + datetime.timedelta(days=offset)
                if day.weekday() in WORKING_DAYS:
                    days.append({"date": day.isoformat(), "banks": [
                        {"bank_name": names[bank], "used": _fmt(used[(bank, day)]), "limit": _fmt(limits[bank])}
                        for bank in sorted(limits)]})
        rows.append({
            "index": index + 1, "start": start.isoformat(), "end": stop.isoformat(), "value": _fmt(total),
            "count": sum(len(group["lines"]) for group in groups),
            "currencies": [{"currency": code, "amount": _fmt(money[0]), "value": _fmt(money[1])}
                           for code, money in sorted(currencies.items(), key=lambda pair: (pair[0] != BASE_CURRENCY, pair[0]))],
            "groups": groups, "days": days,
            "late": any(line["late"] for group in groups for line in group["lines"]),
            "busy": any(day["date"] >= start.isoformat() and day["date"] <= stop.isoformat() for day in busy_days),
        })
    moved = [{"payee": row["payee"], "label": row["label"], "currency": row["currency"], "amount": _fmt(row["amount"]),
              "due": row["due"].isoformat(), "last_day": row["transfers"][-1][0].isoformat(), "split": len(row["transfers"]) > 1}
             for row in sorted(payments, key=lambda row: row["due"])
             if not row["late"] and row["transfers"][-1][0] > _working(row["due"])]
    after = [row for row in payments if row["transfers"][-1][0] > end]
    banks = sorted({account.bank_name for account in CashAccount.objects.filter(business_unit=business_unit, kind=CashAccount.Kind.BANK)
                    .exclude(bank_name="")} | set(names.values()), key=str.lower)
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "today": today.isoformat(), "view": view, "end": end.isoformat(), "periods": rows,
        "total": _fmt(sum((Decimal(row["value"]) for row in rows), ZERO)),
        "late": {"count": sum(1 for row in payments if row["late"]),
                 "value": _fmt(sum((row["value"] for row in payments if row["late"]), ZERO))},
        "busy_days": busy_days, "moved": moved,
        "after_end": {"count": len(after), "value": _fmt(sum((row["value"] for row in after), ZERO))},
        "limits": [{"bank_name": bank, "daily_limit": _fmt(limits[bank.lower()]) if bank.lower() in limits else None,
                    "set_by": next((row.set_by.get_full_name() or row.set_by.username for row in limit_rows
                                    if row.bank_name.lower() == bank.lower()), None)} for bank in banks],
        "accounts": [_account_row(account) | {"is_default": account.is_default}
                     for account in CashAccount.objects.filter(business_unit=business_unit, kind=CashAccount.Kind.BANK)
                     .exclude(account_type__in=[CashAccount.AccountType.LTR, CashAccount.AccountType.LG])],
        "rates": [{"currency": code, "rate": format(rate.normalize(), "f"), "source": source}
                  for code, (rate, source) in known.items() if code != BASE_CURRENCY],
        "planned": [_item_row(item) for item in CashForecastItem.objects.filter(
            business_unit=business_unit, removed_at__isnull=True, direction=CashForecastItem.Direction.OUT)
            .select_related("created_by", "cash_account")],
        "assumptions": {"supplier_days": supplier_days, "periods": count},
        "can_manage": can_manage(actor, business_unit), "currency": BASE_CURRENCY, "policy": POLICY_LABEL,
    }


def set_bank_limit(*, actor, business_unit, data):
    """Set or clear (no amount) one bank's daily foreign-currency transfer limit for the unit, in QAR."""
    _require_manager(actor, business_unit)
    from .access_levels import require_master_admin
    require_master_admin(actor, "change the daily bank transfer limits")
    bank = _text(data.get("bank_name"), "bank name", 80, required=True)
    existing = BankTransferLimit.objects.filter(business_unit=business_unit).annotate(key=Lower("bank_name")).filter(key=bank.lower()).first()
    if data.get("daily_limit") in (None, ""):
        if existing is not None:
            existing.delete()
        return {"bank_name": bank, "daily_limit": None}
    limit = _money(data.get("daily_limit"), "daily limit")
    if limit <= 0:
        raise InvalidTransaction("Give a daily limit above zero, or leave it empty for no limit.")
    if existing is None:
        # Spell the bank as its accounts do.
        account = (CashAccount.objects.filter(business_unit=business_unit, kind=CashAccount.Kind.BANK)
                   .annotate(key=Lower("bank_name")).filter(key=bank.lower()).first())
        existing = BankTransferLimit(business_unit=business_unit, bank_name=account.bank_name if account else bank)
    existing.daily_limit, existing.set_by, existing.set_at = limit, actor, timezone.now()
    existing.save()
    return {"bank_name": existing.bank_name, "daily_limit": _fmt(limit)}
