"""Cash-flow forecast: the bank and cash balance week by week, from what the books already know.

Read-only apart from planned items. Provisional rules (synthetic demo only,
waiting for Finance's approval):

- The forecast starts from today's total on the Bank and cash screen (its
  accounts with their opening balances), or the books' bank and cash balance
  before any account is set up.
- Money in: what customers still owe on each invoice, expected a set number of
  days after the invoice date depending on the customer type: individuals 30,
  companies 60 and government 90 days by default. Anything already past that
  day is expected this week and shown as late.
- Money out: what we still owe suppliers for goods and charges, expected a set
  number of days after the supplier's bill date (or the receipt date when there
  is no bill), 30 by default, less payments already prepared. Prepared supplier
  payments and advances waiting for approval are expected on their payment day
  (this week if that day has passed). Staff expense claims still to repay are
  expected this week.
- Cheques received and not yet cleared are expected on the date written on them, in place of the
  guess from the invoice date. Cheques written are expected out on their date; one written for a
  prepared supplier payment replaces that payment's date.
- Planned items cover what the books cannot know yet, such as rent, salaries
  or a customer's promised payment. They can be once, weekly or monthly.
- Amounts are in QAR; supplier amounts at the rate each receipt was booked at.
  A planned item in another currency counts at the latest rate the books used
  for that currency (a supplier payment, advance or receipt), or an indicative
  rate before there is one.
"""

import calendar
import datetime
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .cheques import forecast_flows as cheque_flows
from .cash_accounts import _require_manager, _require_user, _text, can_manage, cash_overview
from .general_ledger import _day
from .models import (
    BASE_CURRENCY, SUPPORTED_CURRENCIES, CashAccount, CashForecastItem, CustomerType, JournalEntry, JournalLine, PurchaseReceipt,
    SupplierAdvance, SupplierPayment,
)
from .receivables import _open_invoices
from .supplier_payments import _payable
from .transaction_services import InvalidTransaction, _money

ZERO = Decimal("0.00")
MAX_WEEKS = 26
CUSTOMER_DAYS = {CustomerType.INDIVIDUAL: 30, CustomerType.COMPANY: 60, CustomerType.GOVERNMENT: 90}
POLICY_LABEL = ("TEST ONLY provisional rules: customers are expected to pay a set number of days after the invoice, by customer "
                "type (individual 30, company 60, government 90 by default), and we pay "
                "suppliers a set number of days after their bill; anything late is expected this week. Planned items add "
                "what the books cannot know yet. QAR at booked rates.")


# Used only until the books hold a rate for the currency (QAR is pegged to the dollar at 3.64).
INDICATIVE_RATES = {"USD": Decimal("3.64"), "EUR": Decimal("3.95"), "GBP": Decimal("4.60")}


def latest_rate(currency):
    """QAR per one unit: the latest rate the books used, else the indicative rate. Returns (rate, source)."""
    if currency == BASE_CURRENCY:
        return Decimal("1"), "base"
    found = []
    for model in (SupplierPayment, SupplierAdvance, PurchaseReceipt):
        row = (model.objects.filter(currency=currency, rate_date__isnull=False, exchange_rate__gt=0)
               .order_by("-rate_date", "-pk").values_list("rate_date", "exchange_rate").first())
        if row:
            found.append(row)
    if found:
        return max(found)[1], "books"
    return INDICATIVE_RATES[currency], "indicative"


def rates():
    return {code: latest_rate(code) for code, _name, _base in SUPPORTED_CURRENCIES}


def _fmt(value):
    return format((value or ZERO).quantize(Decimal("0.01")), "f")


def _balance(business_unit, code, until):
    sums = JournalLine.objects.filter(
        entry__business_unit=business_unit, entry__status=JournalEntry.Status.POSTED, account__code=code,
        entry__document_date__lte=until,
    ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    return (sums["debit"] or ZERO) - (sums["credit"] or ZERO)


def _days(value, field, default):
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise InvalidTransaction(f"Give the {field} as a whole number of days.") from None
    if not 0 <= number <= 365:
        raise InvalidTransaction(f"The {field} must be from 0 to 365 days.")
    return number


def _add_month(day, months, anchor):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return datetime.date(year, month, min(anchor, calendar.monthrange(year, month)[1]))


def _occurrences(item, start, end):
    """Dates of a planned item from start to end, inclusive."""
    last = min(end, item.until) if item.until else end
    day, count = item.first_date, 0
    while day <= last:
        if day >= start:
            yield day
        if item.repeat == CashForecastItem.Repeat.ONCE:
            return
        count += 1
        day = (item.first_date + datetime.timedelta(weeks=count) if item.repeat == CashForecastItem.Repeat.WEEKLY
               else _add_month(item.first_date, count, item.first_date.day))


def _flows(business_unit, today, customer_days, supplier_days):
    """Every expected movement: (day, direction, source, label, amount, late)."""
    held, flows, covered = cheque_flows(business_unit, today)
    for row in _open_invoices(business_unit, today):
        invoice = row["invoice"]
        # Cheques already received for the order are expected on their own dates instead.
        owed = row["owed"] - held.get(invoice.quote_id, ZERO)
        if owed <= 0:
            continue
        days = customer_days.get(invoice.customer.customer_type, customer_days[CustomerType.INDIVIDUAL])
        due = timezone.localtime(invoice.issued_at).date() + datetime.timedelta(days=days)
        flows.append((max(due, today), "in", "customers", f"{invoice.number} · {invoice.customer.name}", owed, due < today))
    receipts = (PurchaseReceipt.objects.filter(business_unit=business_unit, is_synthetic=True)
                .select_related("supplier").prefetch_related("charges"))
    for receipt in receipts:
        received = timezone.localtime(receipt.created_at).date()
        items = [_payable(receipt)] + [_payable(receipt, charge) for charge in receipt.charges.all() if charge.is_eligible]
        for item in items:
            owed = item.carried - item.cleared()
            prepared = item.payments((SupplierPayment.Status.REQUESTED,))
            # What is still owed and not yet prepared, at the receipt rate.
            open_part = owed - sum((payment.amount * item.rate for payment in prepared), ZERO).quantize(Decimal("0.01"))
            if open_part <= 0:
                continue
            since = item.bill.bill_date if item.charge is None and item.bill is not None else received
            due = since + datetime.timedelta(days=supplier_days)
            what = "goods" if item.charge is None else item.charge.get_category_display().lower()
            flows.append((max(due, today), "out", "suppliers", f"Receipt {receipt.pk} · {receipt.supplier.name} · {what}",
                          open_part, due < today))
    for payment in SupplierPayment.objects.filter(business_unit=business_unit, status=SupplierPayment.Status.REQUESTED,
                                                  advance__isnull=True).select_related("supplier"):
        if payment.pk in covered:
            continue
        flows.append((max(payment.paid_on, today), "out", "prepared", f"Prepared payment to {payment.payee_name} (receipt {payment.receipt_id})",
                      payment.paid_value, False))
    for advance in SupplierAdvance.objects.filter(business_unit=business_unit, status=SupplierAdvance.Status.REQUESTED).select_related("supplier"):
        flows.append((max(advance.paid_on, today), "out", "prepared", f"Advance {advance.number} to {advance.supplier.name}",
                      advance.value, False))
    claims = -_balance(business_unit, "TEST_EXPENSE_CLAIMS", today)
    if claims > 0:
        flows.append((today, "out", "staff", "Staff expense claims still to repay", claims, False))
    return flows


def _item_row(item):
    account = item.cash_account
    return {"id": item.pk, "name": item.name, "direction": item.direction, "amount": _fmt(item.amount), "currency": item.currency,
            "account": {"id": account.pk, "name": account.name, "bank_name": account.bank_name} if account else None,
            "first_date": item.first_date.isoformat(), "repeat": item.repeat, "repeat_label": item.get_repeat_display(),
            "until": item.until.isoformat() if item.until else None,
            "created_by": item.created_by.get_full_name() or item.created_by.username}


SOURCES = {"customers": "Customers paying invoices", "cheques": "Cheques on their dates", "suppliers": "Paying suppliers",
           "prepared": "Payments already prepared",
           "staff": "Repaying staff claims", "planned": "Planned items"}


def cash_forecast(*, actor, business_unit, weeks=13, customer_days=None, supplier_days=None):
    """customer_days maps a customer type to the days its customers take to pay; missing types keep the default."""
    _require_user(actor, business_unit)
    weeks = _days(weeks, "number of weeks", 13)
    if not 4 <= weeks <= MAX_WEEKS:
        raise InvalidTransaction(f"Show from 4 to {MAX_WEEKS} weeks.")
    given = customer_days if isinstance(customer_days, dict) else {}
    customer_days = {kind: _days(given.get(kind), f"days {label.lower()} customers take to pay", CUSTOMER_DAYS[kind])
                     for kind, label in CustomerType.choices}
    supplier_days = _days(supplier_days, "days we take to pay suppliers", 30)
    today = timezone.localdate()
    end = today + datetime.timedelta(weeks=weeks, days=-1)
    flows = _flows(business_unit, today, customer_days, supplier_days)
    items = list(CashForecastItem.objects.filter(business_unit=business_unit, removed_at__isnull=True)
                 .select_related("created_by", "cash_account"))
    known = rates()
    for item in items:
        value = (item.amount * known[item.currency][0]).quantize(Decimal("0.01"))
        for day in _occurrences(item, today, end):
            flows.append((day, item.direction, "planned", item.name, value, False))
    # Same total as the Bank and cash screen once accounts exist (it includes their opening balances).
    # Money not yet placed in an account (no default cash box, say) is still in the business, so it counts too.
    if CashAccount.objects.filter(business_unit=business_unit).exists():
        cash = cash_overview(actor=actor, business_unit=business_unit, start="2000-01-01")
        opening = Decimal(cash["total"]) + (Decimal(cash["unplaced"][-1]["balance"]) if cash["unplaced"] else ZERO)
    else:
        opening = _balance(business_unit, "TEST_CASH", today)
    balance, rows, later = opening, [], {"in": ZERO, "out": ZERO}
    by_source = {key: {"in": ZERO, "out": ZERO} for key in SOURCES}
    lowest = None
    for index in range(weeks):
        start = today + datetime.timedelta(weeks=index)
        stop = start + datetime.timedelta(days=6)
        lines = sorted((flow for flow in flows if start <= flow[0] <= stop), key=lambda flow: (flow[0], flow[1], -flow[4]))
        money_in = sum((flow[4] for flow in lines if flow[1] == "in"), ZERO)
        money_out = sum((flow[4] for flow in lines if flow[1] == "out"), ZERO)
        for flow in lines:
            by_source[flow[2]][flow[1]] += flow[4]
        week_opening, balance = balance, balance + money_in - money_out
        row = {"index": index + 1, "start": start.isoformat(), "end": stop.isoformat(), "opening": _fmt(week_opening),
               "in": _fmt(money_in), "out": _fmt(money_out), "net": _fmt(money_in - money_out), "closing": _fmt(balance),
               "lines": [{"date": day.isoformat(), "direction": direction, "source": source, "source_label": SOURCES[source],
                          "label": label, "amount": _fmt(amount), "late": late}
                         for day, direction, source, label, amount, late in lines]}
        rows.append(row)
        if lowest is None or balance < Decimal(lowest["closing"]):
            lowest = row
    for day, direction, *_rest in flows:
        if day > end:
            later[direction] += _rest[2]
    late = [flow for flow in flows if flow[5]]
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "today": today.isoformat(), "end": end.isoformat(), "weeks": rows,
        "opening": _fmt(opening), "closing": _fmt(balance),
        "lowest": {"week": lowest["index"], "start": lowest["start"], "closing": lowest["closing"]} if lowest else None,
        "goes_negative": any(Decimal(row["closing"]) < 0 for row in rows),
        "by_source": [{"key": key, "label": label, "in": _fmt(by_source[key]["in"]), "out": _fmt(by_source[key]["out"])}
                      for key, label in SOURCES.items()],
        "late": {"in": _fmt(sum((flow[4] for flow in late if flow[1] == "in"), ZERO)),
                 "out": _fmt(sum((flow[4] for flow in late if flow[1] == "out"), ZERO))},
        "later": {key: _fmt(value) for key, value in later.items()},
        "assumptions": {"weeks": weeks, "customer_days": {str(kind): days for kind, days in customer_days.items()},
                        "customer_types": [{"code": code, "label": label} for code, label in CustomerType.choices],
                        "supplier_days": supplier_days},
        "planned": [_item_row(item) for item in items],
        "can_plan": can_manage(actor, business_unit), "currency": "QAR", "policy": POLICY_LABEL,
    }


def add_planned_item(*, actor, business_unit, data):
    _require_manager(actor, business_unit)
    name = _text(data.get("name"), "name", 120, required=True)
    direction = data.get("direction")
    if direction not in CashForecastItem.Direction.values:
        raise InvalidTransaction("Say whether the money comes in or goes out.")
    repeat = data.get("repeat") or CashForecastItem.Repeat.ONCE
    if repeat not in CashForecastItem.Repeat.values:
        raise InvalidTransaction("Choose once, every week or every month.")
    amount = _money(data.get("amount"), "amount")
    if amount <= 0:
        raise InvalidTransaction("Give an amount above zero.")
    currency = data.get("currency") or BASE_CURRENCY
    if currency not in {code for code, _name, _base in SUPPORTED_CURRENCIES}:
        raise InvalidTransaction("Choose QAR, USD, EUR or GBP.")
    account = None
    if data.get("account") not in (None, ""):
        account = CashAccount.objects.filter(pk=data.get("account") if type(data.get("account")) is int else None,
                                             business_unit=business_unit, kind=CashAccount.Kind.BANK).first()
        if account is None:
            raise InvalidTransaction(f"Choose a bank account of {business_unit.name}.")
    first = _day(data.get("first_date"), timezone.localdate(), "first")
    until = _day(data.get("until"), None, "last") if data.get("until") else None
    if until is not None and until < first:
        raise InvalidTransaction("The last date cannot be before the first date.")
    item = CashForecastItem.objects.create(business_unit=business_unit, name=name, direction=direction, amount=amount,
                                           first_date=first, repeat=repeat, until=until, created_by=actor,
                                           currency=currency, cash_account=account)
    return _item_row(item)


def remove_planned_item(*, actor, business_unit, item_id):
    _require_manager(actor, business_unit)
    item = CashForecastItem.objects.filter(pk=item_id, business_unit=business_unit, removed_at__isnull=True).first()
    if item is None:
        raise InvalidTransaction("That planned item was not found.")
    item.removed_by, item.removed_at = actor, timezone.now()
    item.save(update_fields=["removed_by", "removed_at"])
    return {"removed": item.pk}
